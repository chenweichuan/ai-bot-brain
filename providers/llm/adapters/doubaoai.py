import base64
import copy
import json
import filetype
import time
import aiofiles
import httpx
from common.message import stringify_message_content, truncate_media_urls_for_logging
from common.tmp_dir import TmpDir
from common.video import compress_video
from providers.llm.client import LlmClient
from common.log import logger
from config import conf

API_BASE = conf().get("doubaoai_api_base", "")
API_KEY = conf().get("doubaoai_api_key", "")

MODEL_ENDPOINT_REF = {
    "doubao-seed-lite": "doubao-seed-2-0-lite-260428",
    "doubao-seed-turbo": "doubao-seed-2-1-turbo-260628",
    "doubao-seed-pro": "doubao-seed-2-1-pro-260628",
}

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
                                path = await self._get_cached_tmp_file(part["image"]["url"])
                                async with aiofiles.open(path, "rb") as f:
                                    bytes = await f.read()
                                # 使用 filetype 验证是图片
                                kind = filetype.guess(bytes)
                                if not kind or not kind.mime.startswith('image/'):
                                    raise ValueError(f"Not a valid image file")
                                mime = kind.mime
                                b64 = base64.b64encode(bytes).decode("utf-8")
                                part["image"]["url"] = f"data:{mime};base64,{b64}"
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
                                path = await self._get_cached_tmp_file(part["video"]["url"])
                                # 检查视频大小，如果超过指定大小则压缩
                                await compress_video(path, target_size_mb=20)
                                async with aiofiles.open(path, "rb") as f:
                                    bytes = await f.read()
                                # 使用 filetype 验证是视频
                                kind = filetype.guess(bytes)
                                if not kind or not kind.mime.startswith('video/'):
                                    raise ValueError(f"Not a valid video file")
                                mime = kind.mime
                                b64 = base64.b64encode(bytes).decode("utf-8")
                                part["video"]["url"] = f"data:{mime};base64,{b64}"
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

        # 模型名称映射
        request["model"] = MODEL_ENDPOINT_REF.get(request["model"], request["model"])

        headers = {
            "Authorization": f"Bearer {API_KEY}",
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
                            f"{API_BASE}/chat/completions",
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
                        f"{API_BASE}/chat/completions",
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
            "Authorization": f"Bearer {API_KEY}"
        }
        
        async with httpx.AsyncClient() as client:
            with open(params["file"], "rb") as f:
                # 分离文件和其他表单字段，确保所有值都是字符串
                files = {"file": f}
                data = {k: v for k, v in params.items() if k != "file"}
                
                upload_response = await client.post(
                    f"{API_BASE}/files",
                    headers=upload_headers,
                    files=files,
                    data=data,
                    timeout=600.0
                )
                upload_response.raise_for_status()
                upload_result = upload_response.json()
                return upload_result["id"]
    