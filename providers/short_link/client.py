import hashlib
import base64
from common.redis import RedisClient
from common.log import logger
from config import conf


class ShortLinkClient:
    """Short link management module"""
    
    _instance = None
    
    base_url = conf().get("short_link_base_url")
    key_token_to_link = "sl-token:%s-to-link"
    key_md5_to_token = "sl-md5:%s-to-token"
    key_id_generator = "sl-id-generator"

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.redis_client = RedisClient.get_instance()

    async def get_token_by_link(self, link):
        if not link or not link.startswith("http"):
            return ""
        token = ""
        try:
            md5 = hashlib.md5(link.encode()).hexdigest()
            token = await self.redis_client.get(self.key_md5_to_token % md5)
            if not token:
                token = await self._generate_token()
                await self.redis_client.set(self.key_token_to_link % token, link)
                await self.redis_client.set(self.key_md5_to_token % md5, token)
            else:
                token = token.decode() if isinstance(token, bytes) else token
        except Exception as e:
            logger.error(f"[ShortLink] get token by link error: {e}")
        return token

    async def get_link_by_token(self, token):
        link = ""
        try:
            link = await self.redis_client.get(self.key_token_to_link % token)
            if link:
                link = link.decode() if isinstance(link, bytes) else link
        except Exception as e:
            logger.error(f"[ShortLink] get link by token error: {e}")
        return link
    
    async def convert_link_to_short(self, link):
        """Convert link to short link"""
        token = await self.get_token_by_link(link)
        return self.base_url + "/" + token if token else link
    
    async def _generate_token(self):
        """Generate unique token"""
        token = ""
        try:
            id_str = int(await self.redis_client.incr(self.key_id_generator))
            id_b64 = base64.b64encode(id_str.to_bytes(8, byteorder="big")).decode()
            token = id_b64.replace("+", "-").replace("/", "_").lstrip("A").rstrip("=")
        except Exception as e:
            logger.error(f"[ShortLink] generate token error: {e}")
        return token