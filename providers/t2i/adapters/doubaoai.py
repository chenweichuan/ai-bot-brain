"""
doubao image service adapter
"""
import asyncio
import aiofiles

import httpx
import base64
import filetype

from common.log import logger
from providers.storage.client import StorageClient
from common.tmp_dir import TmpDir
from common.token_bucket import TokenBucket
from providers.t2i.client import T2IClient
from config import conf


API_CONFIG = next((p for p in conf().get("model_providers", []) if p["name"] == "doubaoai"), {})


class DoubaoaiT2IAdapter(T2IClient):
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self) -> None:
        super().__init__()
        self.endpoint = f"{API_CONFIG['api_base']}/images/generations"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_CONFIG['api_key']}",
        }
        if conf().get("rate_limit_image"):
            self.tb4image = TokenBucket(conf().get("rate_limit_image", 50))

    async def generate(self, text, model, image_files=None):
        if conf().get("rate_limit_image") and not self.tb4image.get_token():
            raise Exception("请求太快了，请休息一下再问我吧")

        # 提示词
        if image_files:
            logger.info("[DoubaoAI] image_to_image prompt={}, images_count={}".format(text, len(image_files)))
        else:
            logger.info("[DoubaoAI] text_to_image prompt={}".format(text))

        payload = {
            "model": model,
            "prompt": text,
            "sequential_image_generation": "disabled",
            "response_format": "b64_json",
            "size": "2K",
            "stream": False,
            "watermark": False,
        }

        if image_files:
            payload["image"] = []
            for file in image_files:
                path = await TmpDir.save(file)
                async with aiofiles.open(path, "rb") as f:
                    bytes = await f.read()
                # 使用 filetype 验证是图片
                kind = filetype.guess(bytes)
                if not kind or not kind.mime.startswith('image/'):
                    raise ValueError(f"Not a valid image file")
                mime = kind.mime
                b64 = base64.b64encode(bytes).decode("utf-8")
                payload["image"].append(f"data:{mime};base64,{b64}")

        result = None
        retry_cnt = 2
        while retry_cnt:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(self.endpoint, json=payload, headers=self.headers, timeout=120)
                image_bytes = base64.b64decode(response.json()["data"][0]["b64_json"])
                result = StorageClient.path_to_url(await StorageClient.save(image_bytes))
                break
            except Exception as e:
                retry_cnt -= 1
                if image_files:
                    logger.info("[DoubaoAI] image_to_image error={}".format(e))
                else:
                    logger.info("[DoubaoAI] text_to_image error={}".format(e))
                logger.exception(e)
                await asyncio.sleep(5)
                
        return result