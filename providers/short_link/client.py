import hashlib
import secrets
import string
from common.redis import RedisClient
from common.log import logger
from config import conf

class ShortLinkClient:
    """Short link management module"""
    
    _instance = None
    
    ALPHABET = string.ascii_letters + string.digits + "-_"  # 64 chars
    TOKEN_LEN = 6

    base_url = conf().get("short_link_base_url")
    key_token_to_link = "sl-token:%s-to-link"
    key_md5_to_token = "sl-md5:%s-to-token"

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
        except Exception as e:
            logger.error(f"[ShortLink] get token by link error: {e}")
        return token

    async def get_link_by_token(self, token):
        link = ""
        try:
            link = await self.redis_client.get(self.key_token_to_link % token)
        except Exception as e:
            logger.error(f"[ShortLink] get link by token error: {e}")
        return link
    
    async def convert_link_to_short(self, link):
        """Convert link to short link"""
        token = await self.get_token_by_link(link)
        return self.base_url + "/" + token if token else link
    
    async def delete_by_token(self, token):
        """Delete short link mapping by token"""
        if not token:
            return
        try:
            link = await self.redis_client.get(self.key_token_to_link % token)
            await self.redis_client.delete(self.key_token_to_link % token)
            if link:
                md5 = hashlib.md5(link.encode()).hexdigest()
                await self.redis_client.delete(self.key_md5_to_token % md5)
        except Exception as e:
            logger.error(f"[ShortLink] delete by token error: {e}")

    async def delete_by_link(self, link):
        """Delete short link mapping by link"""
        if not link or not link.startswith("http"):
            return
        try:
            md5 = hashlib.md5(link.encode()).hexdigest()
            token = await self.redis_client.get(self.key_md5_to_token % md5)
            await self.redis_client.delete(self.key_md5_to_token % md5)
            if token:
                await self.redis_client.delete(self.key_token_to_link % token)
        except Exception as e:
            logger.error(f"[ShortLink] delete by link error: {e}")

    async def _generate_token(self):
        for _ in range(3):
            token = "".join(secrets.choice(self.ALPHABET) for _ in range(self.TOKEN_LEN))
            if not await self.redis_client.exists(self.key_token_to_link % token):
                return token
        return ""
