class T2IClient:
    """T2I客户端基类"""
    _instance = None
    
    @staticmethod
    def factory(model: str):
        """工厂方法，根据模型名称返回对应的适配器实例"""
        if model.startswith("gemini-"):
            from providers.t2i.adapters.googleai import GoogleaiT2IAdapter
            return GoogleaiT2IAdapter.get_instance()
        else:
            from providers.t2i.adapters.doubaoai import DoubaoaiT2IAdapter
            return DoubaoaiT2IAdapter.get_instance()

    @classmethod
    def get_instance(cls):
        raise NotImplementedError

    async def generate(self, text, model, image_files=None):
        raise NotImplementedError