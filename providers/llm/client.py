import base64
import hashlib
import aiofiles
import filetype
from collections import OrderedDict

from common.tmp_dir import TmpDir
from common.video import compress_video
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
            await compress_video(path, target_size_mb=20)
        async with aiofiles.open(path, "rb") as f:
            raw_bytes = await f.read()
        # 使用 filetype 验证文件类型
        kind = filetype.guess(raw_bytes)
        if not kind or not kind.mime.startswith(mime_prefix):
            raise ValueError(f"Not a valid {media_type} file")
        mime = kind.mime
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        
        return f"data:{mime};base64,{b64}"
