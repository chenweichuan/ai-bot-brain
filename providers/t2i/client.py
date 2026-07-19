from config import conf


# prefix到provider名称的映射
_PREFIX_PROVIDER_MAP = {
    prefix: provider.get("name", "unknown")
    for provider in conf().get("model_providers", [])
    for prefix in provider.get("prefixes", [])
}

class T2IClient:
    """T2I客户端基类"""
    _instance = None
    
    @staticmethod
    def factory(model: str):
        """工厂方法，根据模型名称返回对应的适配器实例"""
        from providers.t2i.adapters import (
            DoubaoaiLlmAdapter,
            GoogleaiT2IAdapter,
        )

        # provider名称到适配器类的映射
        provider_adapter_map = {
            "doubaoai": DoubaoaiLlmAdapter,
            "googleai": GoogleaiT2IAdapter,
        }

        for prefix in _PREFIX_PROVIDER_MAP:
            if model.startswith(prefix):
                adapter_class = provider_adapter_map.get(_PREFIX_PROVIDER_MAP[prefix])
                return adapter_class.get_instance()
        
        raise Exception(f"No corresponding adapter for this model ({model})")

    @classmethod
    def get_instance(cls):
        raise NotImplementedError

    async def generate(self, text, model, image_files=None):
        raise NotImplementedError