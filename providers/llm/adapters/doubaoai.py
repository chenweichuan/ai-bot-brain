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

        # 兼容：推理设置努力程度
        if request.get("thinking") == True:
            request["thinking"] = {
                "type": "enabled"
            }
        elif request.get("thinking") == False:
            request["thinking"] = {
                "type": "disabled"
            }

        headers = {
            "Authorization": f"Bearer {API_CONFIG['api_key']}",
            "Content-Type": "application/json"
        }

        log_request = truncate_media_urls_for_logging(request)
        logger.info(f"[DoubaoAI] LLM request: {json.dumps(log_request, ensure_ascii=False)}")

        if request.get("stream") == True:
            async def process_stream():
                async with httpx.AsyncClient() as client:
                    try:
                        async with client.stream(
                            "POST",
                            f"{API_CONFIG['api_base']}/chat/completions",
                            headers=headers,
                            json=request,
                            timeout=600.0
                        ) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if line.startswith("data:"):
                                    data = line[5:].strip()
                                    if data == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data)
                                        if chunk.get("model"):
                                            chunk["model"] = request["model"]
                                        yield chunk
                                    except json.JSONDecodeError:
                                        continue
                    except Exception as e:
                        logger.error(f"DoubaoAI stream error: {e}")
                        raise

            return process_stream()
        else:
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        f"{API_CONFIG['api_base']}/chat/completions",
                        headers=headers,
                        json=request,
                        timeout=600.0
                    )
                    response.raise_for_status()

                    result = response.json()
                    result["model"] = request["model"]

                    logger.info(f"[DoubaoAI] LLM response: {json.dumps(result, ensure_ascii=False)}")

                    return result
                except Exception as e:
                    logger.error(f"DoubaoAI request error: {e}")
                    raise

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
    