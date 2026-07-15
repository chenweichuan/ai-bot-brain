"""
Memory Service for managing all memory-related operations
"""
from datetime import datetime
from typing import Dict, List, Any, Optional
from common.log import logger
from common.redis import RedisClient
from memory.context_builder import ContextBuilder
from memory.impression_manager import ImpressionManager
from memory.session_manager import SessionManager


class MemoryService:
    """Service that handles all memory-related operations"""
    _instance = None

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.redis_client = RedisClient.get_instance()
        self.impression_manager = ImpressionManager.get_instance()
        self.session_manager = SessionManager.get_instance()
        self.context_builder = ContextBuilder.get_instance()
        logger.info("[MemoryService] Initialized")

    # ==================== Message Management ====================

    async def get_auto_mode_history(self) -> List[Dict[str, Any]]:
        """
        获取自动思考会话消息历史
        """
        from scheduler.inner_mode import InnerModeScheduler
        
        auto_scheduler = InnerModeScheduler.get_instance()
        
        session_id = await self.redis_client.get(auto_scheduler.SESSION_ID_KEY)
        
        return await self.session_manager.get_message_history(session_id=session_id)

    # ==================== Global Memory ====================

    async def get_mixed_memory(self) -> List[List[str]]:
        """
        获取全局记忆（分类、标签、印象）
        """
        return [
            [category for category, _ in list(reversed(await self.impression_manager.get_recent_categories()))],
            [label for label, _ in list(reversed(await self.impression_manager.get_mixed_labels()))],
            [
                f"[{datetime.fromtimestamp(score // 1_000).strftime('%Y-%m-%d %H:%M:%S')}][{pin}][{clue}]{content}" for pin, (clue, content), score 
                in list(reversed(await self.impression_manager.get_mixed_impressions()))
            ],
        ]
        