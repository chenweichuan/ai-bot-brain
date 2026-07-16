import httpx
from contextlib import asynccontextmanager
from common.log import logger
from config import conf


class ProxyClient:
    """Proxy client for managing proxy pool"""
    
    _instance = None

    @classmethod
    def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.session = None
        self.proxy_pool_url = conf().get("proxy_pool_url", "http://127.0.0.1:5010/get/")

    async def get_proxy(self):
        """Get proxy from proxy pool"""
        proxy_api = ""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.proxy_pool_url, timeout=3)
                response.raise_for_status()
                proxy_info = response.json()
                proxy_api = proxy_info["proxy"] if proxy_info.get("proxy") else None
        except Exception as e:
            logger.info(f"[Proxy] get proxy error: {e}")
        return proxy_api

    @asynccontextmanager
    async def stream_request_by_proxy(self, method, target_url, params=None, **kwargs):
        """Make streaming request via proxy with proper context management"""
        client = None
        stream_ctx = None
        try:
            proxy_api = await self.get_proxy()
            if not proxy_api:
                yield None
                return
            
            proxy_url = f"http://{proxy_api}"
            logger.info(f"[Proxy] {method} streaming by proxy: {proxy_url}")
            
            client = httpx.AsyncClient(follow_redirects=True, proxy=proxy_url)
            stream_ctx = client.stream(method, target_url, params=params, **kwargs)
            response = await stream_ctx.__aenter__()
            response.raise_for_status()
            yield response
        except Exception as e:
            logger.info(f"[Proxy] {method} streaming by proxy error: {e}")
            yield None
        finally:
            if stream_ctx:
                try:
                    await stream_ctx.__aexit__(None, None, None)
                except:
                    pass
            if client:
                try:
                    await client.aclose()
                except:
                    pass

    async def request_by_proxy(self, method, target_url, params=None, **kwargs):
        """Make request via proxy (non-streaming)"""
        response = None
        try:
            proxy_api = await self.get_proxy()
            if not proxy_api:
                return
            proxy_url = f"http://{proxy_api}"
            logger.info(f"[Proxy] {method} by proxy: {proxy_url}")
            
            async with httpx.AsyncClient(follow_redirects=True, proxy=proxy_url) as client:
                response = await client.request(method, target_url, params=params, **kwargs)
                response.raise_for_status()
        except Exception as e:
            logger.info(f"[Proxy] {method} by proxy error: {e}")
            response = None
        return response
