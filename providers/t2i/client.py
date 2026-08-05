from common.model import resolve_route


class T2IClient:
    """T2I客户端基类"""
    _instance = None

    @staticmethod
    def factory(model: str):
        """工厂方法，根据模型名称返回对应的适配器实例

        通过 common.model.resolve_route 解析 provider，
        再按命名约定 {ProviderName}T2IAdapter 动态查找适配器类。
        """
        provider, _ = resolve_route(model)
        provider_name = provider["name"]

        # 按命名约定动态查找适配器类: {provider_name.capitalize()}T2IAdapter
        import providers.t2i.adapters as adapters_module
        class_name = f"{provider_name.capitalize()}T2IAdapter"
        adapter_class = getattr(adapters_module, class_name, None)
        if adapter_class is None:
            raise Exception(
                f"No T2I adapter found for provider '{provider_name}' "
                f"(expected class name: {class_name})"
            )

        return adapter_class.get_instance()

    @classmethod
    def get_instance(cls):
        raise NotImplementedError

    async def generate(self, text, model, image_files=None):
        raise NotImplementedError