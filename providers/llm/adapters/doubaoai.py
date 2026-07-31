import copy
import json
import time
import httpx
from common.message import stringify_message_content, truncate_media_urls_for_logging
from providers.llm.client import LlmClient
from common.log import logger
from config import conf

API_CONFIG = next((p for p in conf().get("model_providers", []) if p["name"] == "doubaoai"), {})


class DoubaoaiLlmAdapter(LlmClient):
    _instance = None

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def chat(self, **request):
        request = copy.deepcopy(request)

        for msg in request["messages"]:
            # 非user角色不支持结构化信息，转为纯文本
            if msg["role"] != "user":
                msg["content"] = stringify_message_content(msg.get("content"))
            # user结构化消息里的文件转为base64
            if msg["role"] == "user" and isinstance(msg["content"], list):
                for part in msg["content"]:
                    if part["type"] == "image":
                        try:
                            if not part["image"]["url"].startswith("data:"):
                                part["image"]["url"] = await self._get_base64_data_url(part["image"]["url"], "image")
                            part["type"] = "image_url"
                            part["image_url"] = part["image"]
                            del part["image"]
                        except Exception as e:
                            logger.error(f"Failed to process image: {part['image']['url']}, error: {e}")
                            part["type"] = "text"
                            part["text"] = f"Image Unavailable: {part['image']['url']}"
                            del part["image"]
                    elif part["type"] == "video":
                        try:
                            if not part["video"]["url"].startswith("data:"):
                                part["video"]["url"] = await self._get_base64_data_url(part["video"]["url"], "video")
                            part["type"] = "video_url"
                            part["video_url"] = part["video"]
                            del part["video"]
                        except Exception as e:
                            logger.error(f"Failed to process video: {part['video']['url']}, error: {e}")
                            part["type"] = "text"
                            part["text"] = f"Video Unavailable: {part['video']['url']}"
                            del part["video"]

        headers = {
            "Authorization": f"Bearer {API_CONFIG['api_key']}",
            "Content-Type": "application/json"
        }

        # 转换为 Ark Responses API 请求格式
        ark_request = self._convert_to_ark_request(request)

        log_request = truncate_media_urls_for_logging(request)
        logger.info(f"[DoubaoAI] LLM request: {json.dumps(log_request, ensure_ascii=False)}")

        if request.get("stream") == True:
            async def process_stream():
                async with httpx.AsyncClient() as client:
                    try:
                        async with client.stream(
                            "POST",
                            f"{API_CONFIG['api_base']}/responses",
                            headers=headers,
                            json=ark_request,
                            timeout=600.0
                        ) as response:
                            if response.status_code >= 400:
                                body = await response.aread()
                                logger.error(f"DoubaoAI stream HTTP {response.status_code} error: {body[:500]}")
                                response.raise_for_status()
                            # 累积状态，用于构建 OpenAI 兼容的流式 chunk
                            tool_calls = []  # list of {index, id, type, function: {name, arguments}}
                            response_id = None
                            model_name = request["model"]
                            system_fingerprint = None

                            async for line in response.aiter_lines():
                                if line.startswith("data:"):
                                    data = line[5:].strip()
                                    if data == "[DONE]":
                                        break
                                    try:
                                        event = json.loads(data)
                                        event_type = event.get("type", "")

                                        if event_type == "response.created":
                                            resp = event.get("response", {})
                                            response_id = resp.get("id", "")
                                            if resp.get("model"):
                                                model_name = resp["model"]
                                            # 发送初始 chunk（类似 OpenAI 的第一个 delta 为空的 chunk）
                                            chunk = self._build_initial_chunk(
                                                response_id, model_name, system_fingerprint
                                            )
                                            logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                            yield chunk

                                        elif event_type == "response.output_item.added":
                                            item = event.get("item", {})
                                            item_type = item.get("type", "")

                                            # 处理 function_call 输出
                                            if item_type == "function_call":
                                                tool_call = {
                                                    "index": len(tool_calls),
                                                    "id": item.get("call_id", ""),
                                                    "item_id": item.get("id", ""),  # item id 用于匹配 delta 事件
                                                    "type": "function",
                                                    "function": {
                                                        "name": item.get("name", ""),
                                                        "arguments": ""
                                                    }
                                                }
                                                tool_calls.append(tool_call)
                                                # 发送 tool_calls 初始 chunk
                                                chunk = self._build_tool_call_start_chunk(
                                                    response_id, model_name, tool_call, system_fingerprint
                                                )
                                                logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                                yield chunk
                                            # 处理 reasoning 输出项
                                            elif item_type == "reasoning":
                                                # reasoning 内容通过 response.reasoning_summary_text.delta 事件流式输出
                                                pass

                                        elif event_type == "response.reasoning_summary_text.delta":
                                            # 推理内容增量
                                            delta_reasoning = event.get("delta", "")
                                            if delta_reasoning:
                                                chunk = self._build_reasoning_delta_chunk(
                                                    response_id, model_name, delta_reasoning, system_fingerprint
                                                )
                                                logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                                yield chunk

                                        elif event_type == "response.function_call_arguments.delta":
                                            # function call 参数增量
                                            delta_args = event.get("delta", "")
                                            item_id = event.get("item_id", "")
                                            tc_index = None
                                            for i, tc in enumerate(tool_calls):
                                                # delta 事件的 item_id 匹配 output item 的 id（不是 call_id）
                                                if tc.get("item_id") == item_id:
                                                    tc_index = i
                                                    tc["function"]["arguments"] += delta_args
                                                    break
                                            if tc_index is not None and delta_args:
                                                chunk = self._build_tool_call_delta_chunk(
                                                    response_id, model_name, tc_index, delta_args, system_fingerprint
                                                )
                                                logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                                yield chunk

                                        elif event_type == "response.output_item.done":
                                            # 输出项完成，无需额外处理（OpenAI 格式无对应事件）
                                            pass

                                        elif event_type == "response.completed":
                                            resp = event.get("response", {})
                                            usage = resp.get("usage", {})
                                            status = resp.get("status", "completed")
                                            if resp.get("model"):
                                                model_name = resp["model"]
                                            # 转换 usage 格式
                                            openai_usage = self._convert_usage_from_ark(usage)
                                            if openai_usage:
                                                logger.info(f"[DoubaoAI] Token usage ({model_name}): {json.dumps(openai_usage, ensure_ascii=False)}")
                                            # 发送最终带 usage 和 finish_reason 的 chunk
                                            # 注意：Ark API 即使有 function_call，status 也返回 "completed"
                                            # 需要根据是否累积了 tool_calls 来判断 finish_reason
                                            chunk = self._build_final_chunk(
                                                response_id, model_name, status, openai_usage, system_fingerprint,
                                                has_tool_calls=len(tool_calls) > 0
                                            )
                                            logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                            yield chunk

                                        elif event_type == "response.incomplete":
                                            # 响应不完整（如达到 max_output_tokens 限制）
                                            resp_inc = event.get("response", {})
                                            usage_inc = resp_inc.get("usage", {})
                                            openai_usage_inc = self._convert_usage_from_ark(usage_inc) if usage_inc else {}
                                            if openai_usage_inc:
                                                logger.info(f"[DoubaoAI] Token usage ({model_name}): {json.dumps(openai_usage_inc, ensure_ascii=False)}")
                                            chunk = self._build_final_chunk(
                                                response_id, model_name, "length", openai_usage_inc, system_fingerprint
                                            )
                                            logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                            yield chunk

                                        elif event_type == "response.failed":
                                            error = event.get("error", {})
                                            logger.error(f"DoubaoAI response failed: {json.dumps(error, ensure_ascii=False)}")
                                            raise Exception(f"DoubaoAI API error: {error.get('message', 'Unknown error')}")

                                        elif event_type == "response.output_text.delta":
                                            # 正文文本增量
                                            delta_text = event.get("delta", "")
                                            if delta_text:
                                                chunk = self._build_text_delta_chunk(
                                                    response_id, model_name, delta_text, system_fingerprint
                                                )
                                                logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                                yield chunk

                                        elif event_type == "response.refusal.delta":
                                            # 拒绝内容，作为文本增量的一部分传递
                                            delta_text = event.get("delta", "")
                                            if delta_text:
                                                chunk = self._build_text_delta_chunk(
                                                    response_id, model_name, delta_text, system_fingerprint
                                                )
                                                logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(chunk, ensure_ascii=False)}")
                                                yield chunk

                                        else:
                                            # 忽略未识别的事件类型
                                            pass

                                    except json.JSONDecodeError:
                                        continue
                    except Exception as e:
                        # 尝试读取响应体获取详细错误信息
                        try:
                            resp_text = response.text if 'response' in dir() else ''
                            if resp_text:
                                logger.error(f"DoubaoAI stream error response body: {resp_text[:500]}")
                        except Exception:
                            pass
                        logger.error(f"DoubaoAI stream error: {e}")
                        raise

            return process_stream()
        else:
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        f"{API_CONFIG['api_base']}/responses",
                        headers=headers,
                        json=ark_request,
                        timeout=600.0
                    )
                    if response.status_code >= 400:
                        logger.error(f"DoubaoAI HTTP {response.status_code} error: {response.text[:500]}")
                        response.raise_for_status()

                    ark_result = response.json()

                    # 转换为 OpenAI 兼容的响应格式
                    result = self._convert_from_ark_response(ark_result, request["model"])

                    if result.get("usage"):
                        logger.info(f"[DoubaoAI] Token usage ({result.get('model', request['model'])}): {json.dumps(result['usage'], ensure_ascii=False)}")

                    logger.info(f"[DoubaoAI] LLM response: {json.dumps(result, ensure_ascii=False)}")

                    return result
                except Exception as e:
                    # 尝试读取响应体获取详细错误信息
                    try:
                        if 'response' in dir() and response is not None:
                            resp_text = response.text
                            if resp_text:
                                logger.error(f"DoubaoAI request error response body: {resp_text[:500]}")
                    except Exception:
                        pass
                    logger.error(f"DoubaoAI request error: {e}")
                    raise

    def _convert_to_ark_request(self, request: dict) -> dict:
        """将 OpenAI 格式的请求转换为 Ark Responses API 格式"""
        ark_input = []
        for msg in request["messages"]:
            role = msg["role"]
            content = msg.get("content")

            if role == "assistant":
                # reasoning_content 拆分为独立 reasoning item
                if msg.get("reasoning_content"):
                    ark_input.append({
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": msg["reasoning_content"]}],
                    })
                # content 透传
                if content:
                    ark_input.append({"role": "assistant", "content": content})
                # tool_calls 拆分为 function_call items
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        ark_input.append({
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": tc["function"].get("name", ""),
                            "arguments": tc["function"].get("arguments", ""),
                        })

            elif role == "tool":
                # tool 角色 → function_call_output
                ark_input.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": content if isinstance(content, str) else stringify_message_content(content),
                })

            elif role == "user" and isinstance(content, list):
                # user 结构化 content 需要转换字段名
                ark_content = []
                for part in content:
                    pt = part.get("type", "")
                    if pt == "image_url":
                        img = part.get("image_url", {})
                        url = img.get("url", "") if isinstance(img, dict) else img
                        ark_content.append({"type": "input_image", "image_url": url})
                    elif pt == "video_url":
                        vid = part.get("video_url", {})
                        url = vid.get("url", "") if isinstance(vid, dict) else vid
                        item = {"type": "input_video", "video_url": url}
                        if isinstance(vid, dict) and "fps" in vid:
                            item["fps"] = vid["fps"]
                        ark_content.append(item)
                    elif pt == "text":
                        ark_content.append({"type": "input_text", "text": part.get("text", "")})
                    else:
                        ark_content.append(part)
                ark_input.append({"role": "user", "content": ark_content})
            else:
                # 默认：原始消息不动
                ark_input.append(msg)

        # 最后消息是assistant时必须标注partial
        if ark_input[-1].get("role") == "assistant":
            ark_input[-1]["partial"] = True

        ark = {
            "model": request["model"],
            "input": ark_input,
        }

        # 流式参数
        if request.get("stream") is not None:
            ark["stream"] = request["stream"]

        # 温度
        if request.get("temperature") is not None:
            ark["temperature"] = request["temperature"]

        # top_p
        if request.get("top_p") is not None:
            ark["top_p"] = request["top_p"]

        # presence_penalty
        if request.get("presence_penalty") is not None:
            ark["presence_penalty"] = request["presence_penalty"]

        # frequency_penalty
        if request.get("frequency_penalty") is not None:
            ark["frequency_penalty"] = request["frequency_penalty"]

        # max_tokens → max_output_tokens
        if request.get("max_tokens") is not None:
            ark["max_output_tokens"] = request["max_tokens"]

        # stop
        if request.get("stop") is not None:
            ark["stop"] = request["stop"]

        # thinking / reasoning
        # 上游传 thinking=True/False（布尔值），转换为 Ark API 的 thinking.type 格式
        thinking_val = request.get("thinking")
        if thinking_val is not None:
            if isinstance(thinking_val, bool):
                ark["thinking"] = {"type": "enabled" if thinking_val else "disabled"}
                # 关闭思考时，reasoning.effort 必须为 minimal
                if not thinking_val:
                    ark["reasoning"] = {"effort": "minimal"}
            elif isinstance(thinking_val, dict):
                # 支持直接传 thinking={"type": "enabled"/"disabled"/"auto"}
                ark["thinking"] = thinking_val
                if thinking_val.get("type") == "disabled":
                    ark["reasoning"] = {"effort": "minimal"}

        # reasoning.effort 独立控制思考长度
        if request.get("reasoning_effort") is not None:
            effort = request["reasoning_effort"]
            if effort in ("minimal", "low", "medium", "high"):
                ark["reasoning"] = {"effort": effort}
                # minimal 等同于关闭思考
                if effort == "minimal":
                    ark["thinking"] = {"type": "disabled"}

        # tools
        if request.get("tools") is not None and len(request["tools"]) > 0:
            ark["tools"] = []
            for tool in request["tools"]:
                if tool.get("type") == "function":
                    ark["tools"].append({
                        "type": "function",
                        "name": tool["function"].get("name", ""),
                        "description": tool["function"].get("description", ""),
                        "parameters": tool["function"].get("parameters", {}),
                    })
                else:
                    # 其他类型工具直接透传
                    ark["tools"].append(tool)

        # tool_choice
        if request.get("tool_choice") is not None:
            tool_choice = request["tool_choice"]
            if isinstance(tool_choice, str):
                if tool_choice in ("auto", "none", "required"):
                    ark["tool_choice"] = tool_choice
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                ark["tool_choice"] = {
                    "type": "function",
                    "name": tool_choice["function"].get("name", "")
                }

        # response_format
        if request.get("response_format") is not None:
            rf = request["response_format"]
            if isinstance(rf, dict):
                rf_type = rf.get("type", "text")
                if rf_type == "json_object":
                    ark["text"] = {"format": {"type": "json_object"}}
                elif rf_type == "json_schema":
                    ark["text"] = {
                        "format": {
                            "type": "json_schema",
                            "name": rf.get("json_schema", {}).get("name", "response"),
                            "schema": rf.get("json_schema", {}).get("schema", {}),
                            "strict": rf.get("json_schema", {}).get("strict", False),
                        }
                    }

        # seed
        if request.get("seed") is not None:
            ark["seed"] = request["seed"]

        # user
        if request.get("user") is not None:
            ark["user"] = request["user"]

        return ark

    def _convert_from_ark_response(self, ark_response: dict, model_name: str) -> dict:
        """将 Ark Responses API 响应转换为 OpenAI 格式"""
        output_items = ark_response.get("output", [])

        # 收集文本内容、推理内容和工具调用
        text_content = ""
        reasoning_content = ""
        refusal_content = ""
        tool_calls = []

        for item in output_items:
            item_type = item.get("type", "")
            if item_type == "message":
                # 文本消息
                content = item.get("content", "")
                if isinstance(content, list):
                    # 结构化内容（Ark Responses API 使用 output_text 类型）
                    for part in content:
                        part_type = part.get("type", "")
                        if part_type == "output_text":
                            text_content += part.get("text", "")
                        elif part_type == "text":
                            text_content += part.get("text", "")
                        elif part_type == "refusal":
                            refusal_content += part.get("refusal", "")
                elif isinstance(content, str):
                    text_content += content
            elif item_type == "reasoning":
                # 推理内容（Ark Responses API 使用 summary 数组 + summary_text 类型）
                summary = item.get("summary", [])
                parts_text = []
                if isinstance(summary, list):
                    for part in summary:
                        if part.get("type") == "summary_text":
                            t = part.get("text", "")
                            if t:
                                parts_text.append(t)
                # 兼容：直接 text 字段（某些模型/版本可能返回）
                if not parts_text:
                    t = item.get("text", "")
                    if t:
                        parts_text.append(t)
                if parts_text:
                    if reasoning_content:
                        reasoning_content += "\n\n"
                    reasoning_content += "\n\n".join(parts_text)
            elif item_type == "function_call":
                # 工具调用
                tool_calls.append({
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    }
                })
            elif item_type == "refusal":
                # 拒绝内容
                refusal_content = item.get("refusal", "")

        # 构建 message
        message = {"role": "assistant"}
        if tool_calls:
            message["tool_calls"] = tool_calls
            message["content"] = text_content or None
        else:
            message["content"] = text_content

        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        if refusal_content:
            message["refusal"] = refusal_content

        # 确定 finish_reason
        # 注意：Ark API 即使有 function_call，status 也返回 "completed"
        # 所以需要根据 output 中是否有 function_call 来判断
        status = ark_response.get("status", "completed")
        if tool_calls:
            finish_reason = "tool_calls"
        elif status == "length":
            finish_reason = "length"
        elif status == "content_filter":
            finish_reason = "content_filter"
        else:
            finish_reason = "stop"

        choices = [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }]

        # 转换 usage
        ark_usage = ark_response.get("usage", {})
        usage = self._convert_usage_from_ark(ark_usage)

        # 使用响应中的 model 名称（如果有）
        resp_model = ark_response.get("model", model_name)

        result = {
            "id": ark_response.get("id", ""),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": resp_model,
            "choices": choices,
        }

        if usage:
            result["usage"] = usage

        # service_tier（服务层级）
        if ark_response.get("service_tier"):
            result["service_tier"] = ark_response["service_tier"]

        # system_fingerprint（系统指纹）
        if ark_response.get("system_fingerprint"):
            result["system_fingerprint"] = ark_response["system_fingerprint"]

        return result

    def _convert_usage_from_ark(self, ark_usage: dict) -> dict:
        """将 Ark usage 转换为 OpenAI 格式"""
        if not ark_usage:
            return {}
        usage = {}
        if "input_tokens" in ark_usage:
            usage["prompt_tokens"] = ark_usage["input_tokens"]
        if "output_tokens" in ark_usage:
            usage["completion_tokens"] = ark_usage["output_tokens"]
        if "total_tokens" in ark_usage:
            usage["total_tokens"] = ark_usage["total_tokens"]
        else:
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            if prompt or completion:
                usage["total_tokens"] = prompt + completion
        # 缓存 tokens（Ark API 在 input_tokens_details 中）
        input_details = ark_usage.get("input_tokens_details", {})
        if "cached_tokens" in input_details:
            usage["prompt_tokens_details"] = {
                "cached_tokens": input_details["cached_tokens"]
            }
        # reasoning tokens（Ark API 在 output_tokens_details 中）
        output_details = ark_usage.get("output_tokens_details", {})
        if "reasoning_tokens" in output_details:
            if "completion_tokens_details" not in usage:
                usage["completion_tokens_details"] = {}
            usage["completion_tokens_details"]["reasoning_tokens"] = output_details["reasoning_tokens"]
        return usage

    def _build_initial_chunk(self, response_id: str, model_name: str, system_fingerprint: str = None) -> dict:
        """构建初始流式 chunk（OpenAI 兼容格式）"""
        chunk = {
            "id": response_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }]
        }
        if system_fingerprint:
            chunk["system_fingerprint"] = system_fingerprint
        return chunk

    def _build_text_delta_chunk(self, response_id: str, model_name: str, delta_text: str, system_fingerprint: str = None) -> dict:
        """构建文本增量流式 chunk"""
        chunk = {
            "id": response_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"content": delta_text},
                "finish_reason": None,
            }]
        }
        if system_fingerprint:
            chunk["system_fingerprint"] = system_fingerprint
        return chunk

    def _build_reasoning_delta_chunk(self, response_id: str, model_name: str, delta_reasoning: str, system_fingerprint: str = None) -> dict:
        """构建推理内容增量流式 chunk"""
        chunk = {
            "id": response_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"reasoning_content": delta_reasoning},
                "finish_reason": None,
            }]
        }
        if system_fingerprint:
            chunk["system_fingerprint"] = system_fingerprint
        return chunk

    def _build_tool_call_start_chunk(self, response_id: str, model_name: str, tool_call: dict, system_fingerprint: str = None) -> dict:
        """构建工具调用开始的流式 chunk"""
        chunk = {
            "id": response_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": tool_call["index"],
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {
                            "name": tool_call["function"]["name"],
                            "arguments": ""
                        }
                    }]
                },
                "finish_reason": None,
            }]
        }
        if system_fingerprint:
            chunk["system_fingerprint"] = system_fingerprint
        return chunk

    def _build_tool_call_delta_chunk(self, response_id: str, model_name: str, tc_index: int, delta_args: str, system_fingerprint: str = None) -> dict:
        """构建工具调用参数增量流式 chunk"""
        chunk = {
            "id": response_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": tc_index,
                        "type": "function",
                        "function": {
                            "arguments": delta_args
                        }
                    }]
                },
                "finish_reason": None,
            }]
        }
        if system_fingerprint:
            chunk["system_fingerprint"] = system_fingerprint
        return chunk

    def _build_final_chunk(self, response_id: str, model_name: str, status: str, usage: dict, system_fingerprint: str = None, has_tool_calls: bool = False) -> dict:
        """构建最终流式 chunk（带 finish_reason 和 usage）"""
        # 转换 finish_reason
        # 注意：Ark API 即使有 function_call，status 也返回 "completed"
        if has_tool_calls:
            fr = "tool_calls"
        elif status == "length":
            fr = "length"
        elif status == "content_filter":
            fr = "content_filter"
        else:
            fr = "stop"

        chunk = {
            "id": response_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": fr,
            }]
        }
        if usage:
            chunk["usage"] = usage
        if system_fingerprint:
            chunk["system_fingerprint"] = system_fingerprint
        return chunk

    async def _upload_file(self, params: dict[str, any]) -> str:
        """上传文件到豆包并返回 file_id"""
        upload_headers = {
            "Authorization": f"Bearer {API_CONFIG['api_key']}"
        }

        async with httpx.AsyncClient() as client:
            with open(params["file"], "rb") as f:
                # 分离文件和其他表单字段，确保所有值都是字符串
                files = {"file": f}
                data = {k: v for k, v in params.items() if k != "file"}

                upload_response = await client.post(
                    f"{API_CONFIG['api_base']}/files",
                    headers=upload_headers,
                    files=files,
                    data=data,
                    timeout=600.0
                )
                upload_response.raise_for_status()
                upload_result = upload_response.json()
                return upload_result["id"]