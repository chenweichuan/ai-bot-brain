import base64
import hashlib
import uuid
import aiofiles
import filetype
import os
import asyncio
from PIL import Image, ImageOps

from common.tmp_dir import TmpDir
from common.log import logger
from common.redis import RedisClient
from config import conf


class LlmClient:
    """LLM客户端基类"""

    # prefix到provider名称的映射
    _PREFIX_PROVIDER_MAP = {
        prefix: provider.get("name", "unknown")
        for provider in conf().get("model_providers", [])
        for prefix in provider.get("prefixes", [])
    }

    # Redis中缓存tmp文件映射的key前缀
    _TMP_FILE_CACHE_PREFIX = "llm:tmp_file_cache:"
    # Redis缓存过期时间：7天
    _TMP_FILE_CACHE_TTL = 7 * 24 * 3600

    @staticmethod
    def factory(model: str):
        """工厂方法，根据模型名称返回对应的适配器实例"""
        from providers.llm.adapters import (
            DoubaoaiLlmAdapter,
            ZhipuaiLlmAdapter,
            OpenaiLlmAdapter,
        )

        # provider名称到适配器类的映射
        provider_adapter_map = {
            "doubaoai": DoubaoaiLlmAdapter,
            "zhipuai": ZhipuaiLlmAdapter,
            "openai": OpenaiLlmAdapter,
        }

        for prefix in LlmClient._PREFIX_PROVIDER_MAP:
            if model.startswith(prefix):
                adapter_class = provider_adapter_map.get(LlmClient._PREFIX_PROVIDER_MAP[prefix])
                return adapter_class.get_instance()
        
        raise Exception(f"No corresponding adapter for this model ({model})")

    @classmethod
    def get_instance(cls):
        raise NotImplementedError

    async def chat(self, request):
        raise NotImplementedError
    
    async def _get_base64_data_url(self, url: str, media_type: str) -> str:
        """
        将媒体URL转换为base64 data URL。各适配器共用的数据获取逻辑。

        Args:
            url: HTTP URL 或本地文件路径
            media_type: 媒体类型，"image" 或 "video"

        Returns:
            str: base64 data URL 字符串，格式为 "data:{mime};base64,{b64}"

        Raises:
            ValueError: 文件类型不匹配时抛出
        """
        mime_prefix = f"{media_type}/"
        # 获取已缓存（且已压缩）的文件，或下载并压缩后缓存
        path = await self._get_cached_tmp_file(url, media_type)

        async with aiofiles.open(path, "rb") as f:
            raw_bytes = await f.read()
        # 使用 filetype 验证文件类型
        kind = filetype.guess(raw_bytes)
        if not kind or not kind.mime.startswith(mime_prefix):
            raise ValueError(f"Not a valid {media_type} file")
        mime = kind.mime
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        
        return f"data:{mime};base64,{b64}"

    async def _get_cached_tmp_file(self, source: str, media_type: str = None) -> str:
        """
        获取缓存的临时文件路径，如果不存在则创建并压缩。
        使用Redis存储文件路径映射，缓存的文件已经过压缩处理。
        读取缓存时会检查文件是否仍存在（可能被清理脚本删除），若不存在则重新生成。
        
        Args:
            source: 可以是HTTP URL或本地文件路径
            media_type: 媒体类型，"image" 或 "video"，为None则不压缩
            
        Returns:
            str: 临时文件路径（已压缩）
        """
        # 计算唯一键
        if source.startswith("http"):
            # HTTP URL 使用URL的MD5
            unique_key = hashlib.md5(source.encode("utf-8")).hexdigest()
        else:
            # 本地文件使用文件内容的MD5
            async with aiofiles.open(source, 'rb') as f:
                content = await f.read()
            unique_key = hashlib.md5(content).hexdigest()

        cache_key = f"{self._TMP_FILE_CACHE_PREFIX}{unique_key}"
        redis = RedisClient.get_instance()

        # 检查Redis缓存，并验证文件是否实际存在
        cached_path = await redis.get(cache_key)
        if cached_path and os.path.exists(cached_path):
            logger.info("[LlmClient] reuse cached tmp file for: {}".format(source))
            return cached_path

        # 创建临时文件
        tmp_path = await TmpDir.save(source)

        # 压缩处理
        if media_type == "image":
            await self._compress_image(tmp_path)
        elif media_type == "video":
            await self._compress_video(tmp_path)

        # 写入Redis缓存，设置7天过期
        await redis.set(cache_key, tmp_path, ex=self._TMP_FILE_CACHE_TTL)

        return tmp_path

    async def _compress_image(self, input_path: str) -> None:
        """
        如果图片最短边超过 1080px，则等比缩放使最短边不超过 1080px，直接覆盖原文件
        抛出异常表示压缩失败
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(input_path)

        def _process() -> None:
            with Image.open(input_path) as img:
                # 跳过动图（多帧）
                if getattr(img, "n_frames", 1) > 1:
                    return

                exif = img.info.get("exif", b"")
                icc = img.info.get("icc_profile", b"")
                img = ImageOps.exif_transpose(img)

                width, height = img.size
                short_edge = min(width, height)
                if short_edge <= 1080:
                    logger.info(
                        f"Image already under threshold: {width}x{height}, short edge {short_edge}px <= 1080px"
                    )
                    return

                ratio = 1080 / short_edge
                new_width = int(round(width * ratio)) // 2 * 2
                new_height = int(round(height * ratio)) // 2 * 2

                logger.info(f"Resizing image: {width}x{height} -> {new_width}x{new_height}")

                resized = img.resize((new_width, new_height), Image.LANCZOS)
                ext = os.path.splitext(input_path)[1].lower()
                save_kwargs = {}

                if ext in (".jpg", ".jpeg"):
                    if resized.mode in ("RGBA", "P", "LA", "1", "CMYK"):
                        resized = resized.convert("RGB")
                    save_kwargs.update(
                        quality=85, progressive=True, subsampling=0, exif=exif, icc_profile=icc
                    )
                elif ext == ".png":
                    save_kwargs["optimize"] = True
                    if icc:
                        save_kwargs["icc_profile"] = icc

                tmp_path = f"{input_path}.tmp_{uuid.uuid4().hex[:8]}"
                try:
                    resized.save(tmp_path, **save_kwargs)
                    os.replace(tmp_path, input_path)
                    logger.info(f"Image compressed successfully: {input_path} → {tmp_path}")
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

        await asyncio.to_thread(_process)

    async def _compress_video(self, input_path: str) -> None:
        """
        使用ffmpeg压缩视频并抽帧，直接覆盖原文件
        抛出异常表示压缩失败
        """
        # Validate input file exists
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        input_size = os.path.getsize(input_path)
        logger.info(f"Compressing video: {input_path}, size: {input_size / 1024 / 1024:.2f}MB")

        ext = os.path.splitext(input_path)[1]
        tmp_path = input_path.replace(ext, f"_tmp_{uuid.uuid4().hex[:8]}{ext}")

        try:
            compress_cmd = [
                "ffmpeg",
                "-i", input_path,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "25",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "16k",
                "-ac", "1",
                "-ar", "16000",
                "-vf", "fps=5,scale='min(720,iw)':'-2':force_original_aspect_ratio=decrease",
                "-y",
                tmp_path,
            ]

            compress_proc = await asyncio.create_subprocess_exec(
                *compress_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, compress_stderr = await compress_proc.communicate()

            if compress_proc.returncode != 0:
                raise RuntimeError(f"FFmpeg compression failed: {compress_stderr.decode()}")

            if not os.path.exists(tmp_path):
                raise RuntimeError("Temp output file not created")

            output_size = os.path.getsize(tmp_path)
            logger.info(f"Compressed size: {output_size / 1024 / 1024:.2f}MB")

            os.replace(tmp_path, input_path)
            logger.info(f"Video compressed successfully: {input_path} → {tmp_path}")

        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {tmp_path}: {e}")