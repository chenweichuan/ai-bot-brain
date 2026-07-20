import base64
import hashlib
import aiofiles
import filetype
import os
import asyncio
from collections import OrderedDict

from common.tmp_dir import TmpDir
from common.log import logger
from config import conf


# prefix到provider名称的映射
_PREFIX_PROVIDER_MAP = {
    prefix: provider.get("name", "unknown")
    for provider in conf().get("model_providers", [])
    for prefix in provider.get("prefixes", [])
}

class LlmClient:
    """LLM客户端基类"""
    
    # 文件路径到临时文件路径的映射缓存，使用OrderedDict实现LRU
    _TMP_FILE_CACHE = OrderedDict()
    _MAX_CACHE_SIZE = 100
    
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

        for prefix in _PREFIX_PROVIDER_MAP:
            if model.startswith(prefix):
                adapter_class = provider_adapter_map.get(_PREFIX_PROVIDER_MAP[prefix])
                return adapter_class.get_instance()
        
        raise Exception(f"No corresponding adapter for this model ({model})")

    @classmethod
    def get_instance(cls):
        raise NotImplementedError

    async def chat(self, request):
        raise NotImplementedError
    
    async def _compress_video(self, input_path: str, compress_threshold_mb: int = 30) -> None:
        """
        如果视频超过指定大小（MB）则压缩，直接覆盖原文件
        注意：compress_threshold_mb 是压缩触发阈值，不是精确目标大小
        抛出异常表示压缩失败
        """
        # Validate input file exists
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")
        
        threshold_bytes = compress_threshold_mb * 1024 * 1024
        input_size = os.path.getsize(input_path)
        
        if input_size <= threshold_bytes:
            logger.info(f"Video already under threshold: {input_size / 1024 / 1024:.2f}MB <= {compress_threshold_mb}MB")
            return
        
        logger.info(f"Compressing video: {input_path}, size: {input_size / 1024 / 1024:.2f}MB (threshold: {compress_threshold_mb}MB)")
        
        # Get video duration
        duration_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path
        ]
        duration_proc = await asyncio.create_subprocess_exec(
            *duration_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        duration_stdout, duration_stderr = await duration_proc.communicate()
        if duration_proc.returncode != 0:
            raise RuntimeError(f"Failed to get video duration: {duration_stderr.decode()}")
        
        duration_str = duration_stdout.decode().strip()
        if not duration_str:
            raise RuntimeError("Invalid video duration (empty output)")
        
        duration = float(duration_str)
        if duration <= 0:
            raise RuntimeError(f"Invalid video duration: {duration}")
        
        # Single pass compression (approximate, not exact size target)
        current_bitrate_factor = 0.9
        target_bitrate = int((threshold_bytes * current_bitrate_factor * 8) / duration)
        logger.info(f"Compressing video, target bitrate: {target_bitrate} bps")
        
        ext = os.path.splitext(input_path)[1]
        temp_output_path = input_path.replace(ext, f"_temp{ext}")
        
        try:
            # Compress video with specified video codec
            compress_cmd = [
                "ffmpeg",
                "-i", input_path,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-r", "25",
                "-b:v", str(target_bitrate),
                "-bufsize", str(target_bitrate * 2),
                "-maxrate", str(int(target_bitrate * 1.5)),
                "-c:a", "aac",
                "-b:a", "16k",
                "-ac", "1",
                "-ar", "16000",
                "-y",
                "-vf", "scale='min(1280,iw)':-2",
                temp_output_path
            ]
            
            compress_proc = await asyncio.create_subprocess_exec(
                *compress_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, compress_stderr = await compress_proc.communicate()
            
            if compress_proc.returncode != 0:
                raise RuntimeError(f"FFmpeg compression failed: {compress_stderr.decode()}")
            
            # Check compressed file size
            if not os.path.exists(temp_output_path):
                raise RuntimeError("Temp output file not created")
            
            output_size = os.path.getsize(temp_output_path)
            logger.info(f"Compressed size: {output_size / 1024 / 1024:.2f}MB")
            
            # Replace original file regardless of size (single pass only)
            os.replace(temp_output_path, input_path)
            logger.info(f"Video compressed successfully: {input_path}")
            
        finally:
            # Clean up temp file
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {temp_output_path}: {e}")
    
    async def _get_cached_tmp_file(self, source: str) -> str:
        """
        获取缓存的临时文件路径，如果不存在则创建
        
        Args:
            source: 可以是HTTP URL或本地文件路径
            
        Returns:
            str: 临时文件路径
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
        
        # 检查缓存
        if unique_key in self._TMP_FILE_CACHE:
            # 移到末尾表示最近使用
            self._TMP_FILE_CACHE.move_to_end(unique_key)
            logger.info("[LlmClient] reuse cached tmp file for: {}".format(source))
            return self._TMP_FILE_CACHE[unique_key]
        
        # 不存在则创建
        tmp_path = await TmpDir.save(source)
        self._TMP_FILE_CACHE[unique_key] = tmp_path
        
        # 如果超过最大大小，移除最早的条目
        if len(self._TMP_FILE_CACHE) > self._MAX_CACHE_SIZE:
            self._TMP_FILE_CACHE.popitem(last=False)
        
        return tmp_path

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
        path = await self._get_cached_tmp_file(url)
        
        if media_type == "video":
            # 检查视频大小，如果超过指定大小则压缩
            await self._compress_video(path)
        async with aiofiles.open(path, "rb") as f:
            raw_bytes = await f.read()
        # 使用 filetype 验证文件类型
        kind = filetype.guess(raw_bytes)
        if not kind or not kind.mime.startswith(mime_prefix):
            raise ValueError(f"Not a valid {media_type} file")
        mime = kind.mime
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        
        return f"data:{mime};base64,{b64}"
