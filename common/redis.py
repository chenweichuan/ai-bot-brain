"""
Redis Client - 全局共享的Redis实例
"""
import redis.asyncio as redis
from typing import Optional

from common.log import logger
from config import conf


class RedisClient:
    """
    全局共享的Redis客户端实例
    """
    _instance: Optional[redis.Redis] = None

    @classmethod
    def get_instance(cls) -> redis.Redis:
        """
        获取Redis客户端实例（单例模式）
        
        Returns:
            Redis客户端实例
        """
        return cls._instance

    @classmethod
    async def initialize(cls):
        """
        初始化Redis连接
        """
        try:
            cls._instance = redis.Redis(
                host=conf().get("redis", {}).get("host", "localhost"),
                port=conf().get("redis", {}).get("port", 6379),
                db=conf().get("redis", {}).get("db", 0),
                password=conf().get("redis", {}).get("password") or None,
                decode_responses=True
            )
            # 测试连接
            await cls._instance.ping()
            logger.info("[Redis] Connection established successfully")
        except Exception as e:
            logger.error(f"[Redis] Failed to connect: {e}")
            raise


