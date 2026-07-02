import base64
import copy
import json
import mimetypes
import aiofiles
import httpx
from common.message import stringify_message_content, truncate_media_urls_for_logging
from common.tmp_dir import TmpDir
from common.video import compress_video
from providers.llm.client import LlmClient
from common.log import logger
from config import conf

API_BASE = conf().get("zhipuai_api_base", "")
API_KEY = conf().get("zhipuai_api_key", "")

TEXT_MODEL_ENDPOINT_REF = {
  "glm-4": "glm-4.7",
  "glm-5": "glm-5.2",
}

VISION_MODEL_ENDPOINT_REF = {
  "glm-4": "glm-4.6v",
  "glm-5": "glm-5v-turbo",
}

class ZhipuaiLlmAdapter(LlmClient):
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
                                mime, _ = mimetypes.guess_type(path)
                                mime = mime or "image/png"
                                b64 = base64.b64encode(bytes).decode("utf-8")
                                part["image"]["url"] = f"data:{mime};base64,{b64}"
                            part["type"] = "image_url"
                            part["image_url"] = part["image"]
                            del part["image"]
                        except Exception as e:
                            logger.error(f"Failed to download or read image: {part['image']['url']}, error: {e}")
                            part["type"] = "text"
                            part["text"] = f"Image Unavailable: {part['image']['url']}"
                    elif part["type"] == "video":
                        try:
                            if not part["video"]["url"].startswith("data:"):
                                path = await self._get_cached_tmp_file(part["video"]["url"])
                                # 检查视频大小，如果超过指定大小则压缩
                                await compress_video(path, target_size_mb=20)
                                async with aiofiles.open(path, "rb") as f:
                                    bytes = await f.read()
                                mime, _ = mimetypes.guess_type(path)
                                mime = mime or "video/mp4"
                                b64 = base64.b64encode(bytes).decode("utf-8")
                                part["video"]["url"] = f"data:{mime};base64,{b64}"
                            part["type"] = "video_url"
                            part["video_url"] = part["video"]
                            del part["video"]
                        except Exception as e:
                            logger.error(f"Failed to download or read video: {part['video']['url']}, error: {e}")
                            part["type"] = "text"
                            part["text"] = f"Video Unavailable: {part['video']['url']}"
                            del part["video"]

        # 默认关闭内置的搜索
        request["tools"] = request.get("tools") or []
        if not any(tool.get("type") == "web_search" for tool in request["tools"]):
            request["tools"].append({
                "type": "web_search",
                "web_search": {
                    "enable": False,
                },
            })

        # 工具调用也流式输出
        request["tool_stream"] = request.get("stream")

        # 兼容：推理设置努力程度
        if request.get("thinking") == True:
            request["thinking"] = {
                "type": "enabled",
            }
        elif request.get("thinking") == False:
            request["thinking"] = {
                "type": "disabled",
            }


        has_visual_content = any(
            isinstance(msg.get("content"), list)
            and any((part or {}).get("type") in ["image_url", "video_url"] for part in msg.get("content", []))
            for msg in request.get("messages", [])
        )
        if has_visual_content:
            # 视觉模型名称映射
            request["model"] = VISION_MODEL_ENDPOINT_REF.get(request["model"], request["model"])
        else:
            # 文本模型名称映射
            request["model"] = TEXT_MODEL_ENDPOINT_REF.get(request["model"], request["model"])

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        log_request = truncate_media_urls_for_logging(request)
        logger.info(f"[ZhipuAI] LLM request: {json.dumps(log_request, ensure_ascii=False)}")

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
                                        yield json.loads(data)
                                    except json.JSONDecodeError:
                                        continue
                    except Exception as e:
                        logger.error(f"ZhipuAI stream error: {e}")
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

                    logger.info(f"[ZhipuAI] LLM response: {json.dumps(result, ensure_ascii=False)}")

                    return result
                except Exception as e:
                    logger.error(f"ZhipuAI request error: {e}")
                    raise
