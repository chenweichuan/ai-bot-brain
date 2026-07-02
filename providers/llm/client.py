import hashlib
import aiofiles
from collections import OrderedDict

from common.tmp_dir import TmpDir
from common.log import logger


class LlmClient:
    """LLM客户端基类"""
    
    # 文件路径到临时文件路径的映射缓存，使用OrderedDict实现LRU
    _tmp_file_cache = OrderedDict()
    _MAX_CACHE_SIZE = 100
    
    @staticmethod
    def factory(model: str):
        """工厂方法，根据模型名称返回对应的适配器实例"""
        if model.startswith("gpt-"):
            from providers.llm.adapters.openai import OpenaiLlmAdapter
            return OpenaiLlmAdapter.get_instance()
        elif model.startswith("glm-"):
            from providers.llm.adapters.zhipuai import ZhipuaiLlmAdapter
            return ZhipuaiLlmAdapter.get_instance()
        else:
            from providers.llm.adapters.doubaoai import DoubaoaiLlmAdapter
            return DoubaoaiLlmAdapter.get_instance()

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
        if unique_key in self._tmp_file_cache:
            # 移到末尾表示最近使用
            self._tmp_file_cache.move_to_end(unique_key)
            logger.info("[LlmClient] reuse cached tmp file for: {}".format(source))
            return self._tmp_file_cache[unique_key]
        
        # 不存在则创建
        tmp_path = await TmpDir.save(source)
        self._tmp_file_cache[unique_key] = tmp_path
        
        # 如果超过最大大小，移除最早的条目
        if len(self._tmp_file_cache) > self._MAX_CACHE_SIZE:
            self._tmp_file_cache.popitem(last=False)
        
        return tmp_path
