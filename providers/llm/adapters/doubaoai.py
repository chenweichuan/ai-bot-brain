import copy
import json
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
        is_completions_format = "messages" in request

        # Completions 格式转换为 Responses 请求格式
        if is_completions_format:
            request = await self._convert_to_responses_request(request)

        headers = {
            "Authorization": f"Bearer {API_CONFIG['api_key']}",
            "Content-Type": "application/json"
        }

        log_request = truncate_media_urls_for_logging(request)
        logger.info(f"[DoubaoAI] LLM request: {json.dumps(log_request, ensure_ascii=False)}")

        if request.get("stream") == True:
            async def process_stream():
                async with httpx.AsyncClient() as client:
                    response = None
                    try:
                        async with client.stream(
                            "POST",
                            f"{API_CONFIG['api_base']}/responses",
                            headers=headers,
                            json=request,
                            timeout=600.0
                        ) as response:
                            if response.status_code >= 400:
                                body = await response.aread()
                                logger.error(f"DoubaoAI stream HTTP {response.status_code} error: {body[:500]}")
                                response.raise_for_status()

                            # 累积状态，用于构建 Completions 兼容的流式 chunk
                            common_fields = {
                                "created": None,
                                "id": None,
                                "model": request["model"],
                                "service_tier": None,
                            }
                            has_reasoning_content = False
                            has_content = False
                            tool_call_item_ids = []

                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue

                                data = line[5:].strip()
                                if data == "[DONE]":
                                    break

                                try:
                                    event = json.loads(data)
                                    event_type = event.get("type", "")

                                    logger.info(f"[DoubaoAI] LLM response chunk: {json.dumps(event, ensure_ascii=False)}")
                                    if event.get("response", {}).get("usage"):
                                        logger.info(f"[DoubaoAI] Token usage ({request['model']}): {json.dumps(event['response']['usage'], ensure_ascii=False)}")

                                    # Responses 格式直接透传给上游，否则转换为 Completions 格式
                                    if not is_completions_format:
                                        yield event
                                        continue

                                    if event_type == "response.created":
                                        result = event.get("response", {})
                                        common_fields["created"] = result.get("created_at")
                                        common_fields["id"] = result.get("id")
                                        common_fields["model"] = result.get("model") or request["model"]
                                        common_fields["service_tier"] = result.get("service_tier")

                                    elif event_type == "response.reasoning_summary_part.added" and event.get("part", {}).get("type") == "summary_text":
                                        if not has_reasoning_content:
                                            has_reasoning_content = True
                                        else:
                                            # 如果已经有推理内容片段了，插入新片段换行
                                            yield self._create_completions_chunk(reasoning_content="\n\n", **common_fields)

                                    elif event_type == "response.reasoning_summary_text.delta":
                                        # 推理内容增量
                                        delta_reasoning = event.get("delta", "")
                                        if delta_reasoning:
                                            yield self._create_completions_chunk(reasoning_content=delta_reasoning, **common_fields)

                                    elif event_type == "response.content_part.added" and event.get("part", {}).get("type") == "output_text":
                                        if not has_content:
                                            has_content = True
                                        else:
                                            # 如果已经有文本内容片段了，插入新片段换行
                                            yield self._create_completions_chunk(content="\n\n", **common_fields)

                                    elif event_type == "response.output_text.delta":
                                        # 正文文本增量
                                        delta_text = event.get("delta", "")
                                        if delta_text:
                                            yield self._create_completions_chunk(content=delta_text, **common_fields)

                                    elif event_type == "response.output_item.added" and event.get("item", {}).get("type") == "function_call":
                                        # 新增 tool call 结构
                                        item = event.get("item", {})
                                        tool_call_item_ids.append(item.get("id", ""))
                                        tool_call = {
                                            "function": {
                                                "arguments": "",
                                                "name": item.get("name", ""),
                                            },
                                            "id": item.get("call_id", ""),
                                            "index": len(tool_call_item_ids) - 1,
                                            "type": "function",
                                        }
                                        yield self._create_completions_chunk(tool_call=tool_call, **common_fields)

                                    elif event_type == "response.function_call_arguments.delta":
                                        # tool call 参数增量
                                        delta_args = event.get("delta", "")
                                        item_id = event.get("item_id", "")
                                        tc_index = tool_call_item_ids.index(item_id) if item_id in tool_call_item_ids else None 
                                        if tc_index is not None and delta_args:
                                            tool_call = { "function": { "arguments": delta_args }, "index": tc_index }
                                            yield self._create_completions_chunk(tool_call=tool_call, **common_fields)

                                    elif event_type == "response.completed":
                                        result = event.get("response", {})
                                        usage = self._convert_to_completions_usage(result.get("usage"))
                                        finish_reason = "tool_calls" if len(tool_call_item_ids) > 0 else "stop"
                                        yield self._create_completions_chunk(finish_reason=finish_reason, usage=usage, **common_fields)

                                    elif event_type == "response.incomplete":
                                        # 响应不完整（如达到 max_output_tokens 限制）
                                        result = event.get("response", {})
                                        usage = self._convert_to_completions_usage(result.get("usage"))
                                        finish_reason = result.get("incomplete_details", {}).get("reason", "length")
                                        yield self._create_completions_chunk(finish_reason=finish_reason, usage=usage, **common_fields)

                                except json.JSONDecodeError:
                                    continue
                    except Exception as e:
                        # 尝试读取响应体获取详细错误信息
                        try:
                            response_text = response.text if response else ''
                            if response_text:
                                logger.error(f"[DoubaoAI] stream error response body: {response_text[:500]}")
                        except Exception:
                            pass
                        logger.error(f"[DoubaoAI] stream error: {e}")
                        raise

            return process_stream()
        else:
            async with httpx.AsyncClient() as client:
                response = None
                try:
                    response = await client.post(
                        f"{API_CONFIG['api_base']}/responses",
                        headers=headers,
                        json=request,
                        timeout=600.0
                    )
                    if response.status_code >= 400:
                        logger.error(f"[DoubaoAI] HTTP {response.status_code} error: {response.text[:500]}")
                        response.raise_for_status()

                    result = response.json()

                    logger.info(f"[DoubaoAI] LLM response: {json.dumps(result, ensure_ascii=False)}")
                    if result.get("usage"):
                        logger.info(f"[DoubaoAI] Token usage ({request['model']}): {json.dumps(result['usage'], ensure_ascii=False)}")

                    # 转换为 Completions 的响应格式
                    if is_completions_format:
                        result = self._convert_to_completions_response(result, request["model"])

                    return result
                except Exception as e:
                    # 尝试读取响应体获取详细错误信息
                    try:
                        response_text = response.text if response else ''
                        if response_text:
                            logger.error(f"[DoubaoAI] request error response body: {response_text[:500]}")
                    except Exception:
                        pass
                    logger.error(f"[DoubaoAI] request error: {e}")
                    raise

    async def _convert_to_responses_request(self, request: dict) -> dict:
        """将 Completions 格式的请求转换为 Responses 格式"""
        for msg in request["messages"]:
            # 非user角色不支持结构化信息，转为纯文本
            if msg["role"] != "user":
                msg["content"] = stringify_message_content(msg.get("content"))
            # user结构化消息里自定义的参数格式进行转换
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

        new_input = []
        for msg in request["messages"]:
            role = msg["role"]

            if role == "assistant":
                # reasoning_content 拆分为独立 reasoning item
                if msg.get("reasoning_content"):
                    new_input.append({
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": msg["reasoning_content"]}],
                    })
                # content 透传
                if msg.get("content"):
                    new_input.append({"role": "assistant", "content": msg["content"]})
                # tool_calls 拆分为 function_call items
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        new_input.append({
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": tc["function"].get("name", ""),
                            "arguments": tc["function"].get("arguments", ""),
                        })

            elif role == "tool":
                # tool 角色 → function_call_output
                new_input.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content"),
                })

            elif role == "user" and isinstance(msg.get("content"), list):
                # user 结构化 content 需要转换字段名
                new_content = []
                for part in msg.get("content"):
                    pt = part.get("type", "")
                    if pt == "image_url":
                        img = part.get("image_url", {})
                        url = img.get("url", "") if isinstance(img, dict) else img
                        new_content.append({"type": "input_image", "image_url": url})
                    elif pt == "video_url":
                        vid = part.get("video_url", {})
                        url = vid.get("url", "") if isinstance(vid, dict) else vid
                        item = {"type": "input_video", "video_url": url}
                        if isinstance(vid, dict) and "fps" in vid:
                            item["fps"] = vid["fps"]
                        new_content.append(item)
                    elif pt == "text":
                        new_content.append({"type": "input_text", "text": part.get("text", "")})
                    else:
                        new_content.append(part)
                new_input.append({"role": "user", "content": new_content})
            else:
                # 默认：原始消息不动
                new_input.append(msg)

        # 最后消息是assistant时必须标注partial
        if new_input[-1].get("role") == "assistant":
            new_input[-1]["partial"] = True

        new_request = {
            "model": request["model"],
            "input": new_input,
        }
        
        for key in ["stream", "temperature", "top_p", "presence_penalty", "frequency_penalty", "max_output_tokens", "stop"]:
            if request.get(key) is not None:
                new_request[key] = request.get(key)

        # thinking / reasoning
        # 上游传 thinking=True/False（布尔值），转换为 Responses 的 thinking.type 格式
        thinking = request.get("thinking")
        if thinking is not None:
            if isinstance(thinking, bool):
                new_request["thinking"] = {"type": "enabled" if thinking else "disabled"}
                # 关闭思考时，reasoning.effort 必须为 minimal
                if not thinking:
                    new_request["reasoning"] = {"effort": "minimal"}
            elif isinstance(thinking, dict):
                # 支持直接传 thinking={"type": "enabled"/"disabled"/"auto"}
                new_request["thinking"] = thinking
                if thinking.get("type") == "disabled":
                    new_request["reasoning"] = {"effort": "minimal"}

        # reasoning.effort 独立控制思考长度
        if request.get("reasoning_effort") is not None:
            effort = request["reasoning_effort"]
            new_request["reasoning"] = {"effort": effort}
            # minimal 等同于关闭思考
            if effort == "minimal":
                new_request["thinking"] = {"type": "disabled"}

        # tools
        if request.get("tools"):
            new_request["tools"] = []
            for tool in request["tools"]:
                if tool.get("type") == "function":
                    new_request["tools"].append({
                        "type": "function",
                        "name": tool["function"].get("name", ""),
                        "description": tool["function"].get("description", ""),
                        "parameters": tool["function"].get("parameters", {}),
                    })
                else:
                    # 其他类型工具直接透传
                    new_request["tools"].append(tool)

        # tool_choice
        if request.get("tool_choice") is not None:
            tool_choice = request["tool_choice"]
            if isinstance(tool_choice, str):
                new_request["tool_choice"] = tool_choice
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                new_request["tool_choice"] = {
                    "type": "function",
                    "name": tool_choice["function"].get("name", "")
                }

        # response_format
        if request.get("response_format"):
            new_request["text"] = {"format": request["response_format"]}

        return new_request

    def _convert_to_completions_response(self, response: dict, model: str) -> dict:
        """将 Responses 响应转换为 Completions 格式"""
        output_items = response.get("output", [])

        # 收集推理内容、文本内容和工具调用
        reasoning_content_parts = []
        content_parts = []
        tool_calls = []

        for item in output_items:
            item_type = item.get("type", "")
            if item_type == "reasoning":
                # 推理内容（Responses 使用 summary 数组 + summary_text 类型）
                summary = item.get("summary", [])
                for part in summary:
                    if part.get("type") == "summary_text":
                        reasoning_content_parts.append(part.get("text", ""))
            elif item_type == "message":
                # 文本消息
                content = item.get("content", "")
                # 结构化内容（Responses 使用 output_text 类型）
                for part in content:
                    if part.get("type") == "output_text":
                        content_parts.append(part.get("text", ""))
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

        # 构建 message
        message = {
            "role": "assistant",
            "reasoning_content": "\n\n".join(reasoning_content_parts),
            "content": "\n\n".join(content_parts),
            "tool_calls": tool_calls,
        }

        # 确定 finish_reason
        # 注意：Responses 即使有 function_call，status 也返回 "completed"
        # 所以需要根据是否有 tool_calls 来判断
        status = response.get("status")
        if tool_calls:
            finish_reason = "tool_calls"
        elif status == "incomplete":
            finish_reason = response.get("incomplete_details", {}).get("reason", "length")
        else:
            finish_reason = "stop"

        # 转换 usage
        usage = response.get("usage", {})
        usage = self._convert_to_completions_usage(usage)

        # 使用响应中的 model 名称（如果有）
        model = response.get("model", model)

        result = {
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "created": response.get("created_at"),
            "id": response.get("id", ""),
            "model": model,
            "service_tier": response.get("service_tier", ""),
            "object": "chat.completion",
            "usage": usage,
        }

        return result

    def _create_completions_chunk(
        self,
        reasoning_content: str = None,
        content: str = None,
        tool_call: dict = None,
        finish_reason: str = None,
        created: str = None,
        id: str = None,
        model: str = None,
        service_tier: str = None,
        usage: dict = None,
    ) -> dict:
        """将 Responses Chunk 响应转换为 Completions Chunk 格式"""
        delta = { "role": "assistant" }

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content
        if content:
            delta["content"] = content
        if tool_call:
            delta["tool_calls"] = [tool_call]
        
        result = {
            "choices": [{
                "delta": delta,
                "index": 0,
            }],
            "created": created,
            "id": id,
            "model": model,
            "service_tier": service_tier,
            "object":"chat.completion.chunk",
            "usage": usage,
        }
        
        if finish_reason:
            result["choices"][0]["finish_reason"] = finish_reason

        return result

    def _convert_to_completions_usage(self, usage: dict) -> dict:
        """将 Responses usage 转换为 Completions 格式"""
        return {
            "completion_tokens": usage.get("output_tokens", 0),
            "prompt_tokens": usage.get("input_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "prompt_tokens_details": usage.get("input_tokens_details", {}),
            "completion_tokens_details": usage.get("output_tokens_details", {}),
        } if usage else None
