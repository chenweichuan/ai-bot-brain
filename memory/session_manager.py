"""
Session Manager - 用于管理会话消息历史
使用Redis队列存储消息，通过session_id读取和写入消息历史
"""
import uuid
import json
import time
from typing import Any, List, Dict, Optional

from common.log import logger
from common.redis import RedisClient
from config import conf


class SessionManager:
    """
    会话管理器，负责：
    1. 生成唯一的session_id
    2. 存储消息到Redis队列
    3. 通过session_id获取消息历史
    4. 管理用户的会话列表
    """
    _instance: Optional['SessionManager'] = None
    
    USER_SESSIONS_PER_SET = 100
    MESSAGES_PER_SET = 100
    
    @classmethod
    def get_instance(cls) -> 'SessionManager':
        """
        Get singleton instance of SessionManager
        
        Returns:
            SessionManager instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.redis_client = RedisClient.get_instance()
        
        self.bot_name = conf().get("bot_name", "Bot")
        self.KEY_PREFIX = f"{self.bot_name.lower().replace(' ', '_')}:session"
        
        self.USER_SESSIONS_ZSET_KEY = f"{self.KEY_PREFIX}:user:sessions:%s"
        self.MESSAGE_IDS_ZSET_KEY = f"{self.KEY_PREFIX}:message_ids:%s"
        self.MESSAGE_KEY = f"{self.KEY_PREFIX}:message:%s"
        
        self.SESSION_LAST_ACTIVE_TIME_KEY = f"{self.KEY_PREFIX}:last_active_time:%s"
        self.GLOBAL_LAST_ACTIVE_TIME_KEY = f"{self.KEY_PREFIX}:global_last_active_time"
        
    def generate_session_id(self) -> str:
        """
        生成唯一的session_id
        
        Returns:
            唯一的session_id字符串
        """
        return str(uuid.uuid4())
    
    def create_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建新消息，为其添加唯一ID和时间戳
        
        Args:
            message: 原始消息字典，需包含role和content字段
        
        Returns:
            包含ID和时间戳的新消息字典
        """
        message = message.copy()
        message["id"] = str(uuid.uuid4())
        message["timestamp"] = time.time_ns() / 1_000_000
        return message
    
    async def save_message(self, session_id: str, message: Dict) -> None:
        """
        向指定会话添加消息
        
        Args:
            session_id: 会话ID
            message: 消息字典，需包含role和content字段
        """
        
        if not session_id:
            raise ValueError("Session ID must be provided")
        
        if not message.get("id") or not message.get("timestamp"):
            raise ValueError("Message must contain 'id' and 'timestamp' fields")
        
        # 如果消息推理为空、内容为空且没有工具调用，则不保存该消息
        if not message.get("reasoning_content") and not message.get("content") and not message.get("tool_calls"):
            return
        
        try:
            message_id = message["id"]
            timestamp = message["timestamp"]
            
            # 设置消息的最后修改时间
            message["mod_time"] = time.time_ns() / 1_000_000
            
            # 使用pipeline进行原子操作
            pipe = await self.redis_client.pipeline()
            
            # 将消息ID添加到会话的消息ID有序集合
            message_ids_key = self.MESSAGE_IDS_ZSET_KEY % session_id
            if await self.redis_client.zscore(message_ids_key, message_id) is None:
                await pipe.zadd(message_ids_key, {message_id: timestamp})
                await pipe.zremrangebyrank(message_ids_key, 0, -1_001)
            
            # 将消息内容单独存储
            await pipe.set(self.MESSAGE_KEY % message_id, json.dumps(message, ensure_ascii=False))
            
            # 执行pipeline
            await pipe.execute()
            
            logger.debug(f"[SessionManager] Added message (ID: {message_id}) to session {session_id}")
        except Exception as e:
            logger.error(f"[SessionManager] Failed to add message to session {session_id}: {e}")
            raise
    
    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        获取指定消息ID的消息内容
        
        Args:
            message_id: 消息ID
        
        Returns:
            消息字典，如果消息不存在则返回None
        """
        try:
            message_key = self.MESSAGE_KEY % message_id
            message_str = await self.redis_client.get(message_key)
            if message_str:
                return json.loads(message_str)
            else:
                logger.warning(f"[SessionManager] Message content not found for ID {message_id}")
                return None
        except json.JSONDecodeError:
            logger.error(f"[SessionManager] Invalid JSON message for ID {message_id}")
            return None
    
    async def multi_get_messages(self, message_ids: List[str]) -> List[Dict[str, Any]]:
        """
        批量获取多个消息ID的消息内容
        
        Args:
            message_ids: 消息ID列表
        
        Returns:
            包含所有消息内容的列表，按输入顺序排列
        """
        messages = []
        for message_id in message_ids:
            message = await self.get_message(message_id)
            if message:
                messages.append(message)

        return messages
    
    async def get_message_history(
        self,
        session_id: str,
        from_message_id: str = None,
        after_message_id: str = None,
        limit: int = MESSAGES_PER_SET
    ) -> List[Dict]:
        """
        获取指定会话的消息历史（从远到近排序）
        
        Args:
            session_id: 会话ID
            limit: 返回的最大消息数
        
        Returns:
            消息历史列表，按时间顺序从远到近排列
        """
        try:
            message_ids_key = self.MESSAGE_IDS_ZSET_KEY % session_id
            
            # 获取最新的limit条消息ID（按时间戳升序排列，从远到近）
            message_ids = await self.redis_client.zrange(message_ids_key, -limit, -1)

            # 如果有from_message_id，从该ID开始过滤
            if from_message_id:
                from_index = message_ids.index(from_message_id) if from_message_id in message_ids else None
                message_ids = message_ids[from_index:] if from_index is not None else []
                    
            # 如果有after_message_id，从该ID之后过滤
            if after_message_id:
                after_index = message_ids.index(after_message_id) if after_message_id in message_ids else None
                message_ids = message_ids[after_index + 1:] if after_index is not None else []
            
            if not message_ids:
                return []
            
            # 批量获取消息内容
            messages = await self.multi_get_messages(message_ids)

            return messages
        except Exception as e:
            logger.error(f"[SessionManager] Failed to get message history for session {session_id}: {e}")
            raise
    
    async def save_user_session(self, username: str, session_id: str) -> None:
        """
        将会话ID添加到用户的会话列表
        
        Args:
            username: 用户名
            session_id: 会话ID
        """
        if not username or not session_id:
            raise ValueError("Username and session_id must be provided")
        
        try:
            key = self.USER_SESSIONS_ZSET_KEY % username.lower().replace(' ', '_')
            
            # 获取当前时间戳
            timestamp = time.time_ns() / 1_000_000
            
            # 使用pipeline进行原子操作
            pipe = await self.redis_client.pipeline()
            
            # 将新会话ID添加到有序集合（最新的在最前面，已存在的会被更新）
            await pipe.zadd(key, {session_id: timestamp})
            await pipe.zremrangebyrank(key, 0, -1_001)
            
            # 执行pipeline
            await pipe.execute()
            
            logger.debug(f"[SessionManager] Added session {session_id} to user {username}'s session list")
        except Exception as e:
            logger.error(f"[SessionManager] Failed to add session {session_id} to user {username}: {e}")
            raise
   
    async def check_user_session(self, username: str, session_id: str) -> bool:
        """
        指定会话是否属于指定用户
        
        Args:
            username: 用户名
            session_id: 会话ID
            
        Returns:
            True表示属于用户，False表示不属于用户
        """
        try:
            key = self.USER_SESSIONS_ZSET_KEY % username.lower().replace(' ', '_')
            
            # 使用ZSCORE检查会话ID是否存在
            timestamp = await self.redis_client.zscore(key, session_id)
            
            return timestamp is not None
        except Exception as e:
            logger.error(f"[SessionManager] Failed to check user session: {e}")
            raise

    async def set_session_last_active_time(self, session_id: str, timestamp: float = None) -> float:
        """
        设置会话的最后活动时间
        
        Args:
            session_id: 会话ID
            timestamp: 最后活动时间戳（可选，默认当前时间）
        """
        if not session_id:
            raise ValueError("Session ID must be provided")
        
        try:
            key = self.SESSION_LAST_ACTIVE_TIME_KEY % session_id
            
            # 如果未提供时间戳，使用当前时间
            if timestamp is None:
                timestamp = time.time_ns() / 1_000_000
            
            # 将最后活动时间存储在Redis中
            await self.redis_client.set(key, timestamp)
            await self.redis_client.set(self.GLOBAL_LAST_ACTIVE_TIME_KEY, timestamp)
            
            logger.debug(f"[SessionManager] Set last active time {timestamp} for session {session_id}")
            return timestamp
        except Exception as e:
            logger.error(f"[SessionManager] Failed to set last active time for session {session_id}: {e}")
            raise
    
    async def get_session_last_active_time(self, session_id: str) -> Optional[float]:
        """
        获取会话的最后活动时间
        
        Args:
            session_id: 会话ID
            
        Returns:
            最后活动时间戳，如果不存在则返回None
        """
        try:
            key = self.SESSION_LAST_ACTIVE_TIME_KEY % session_id
            
            # 从Redis获取最后活动时间
            timestamp_str = await self.redis_client.get(key)
            
            if timestamp_str is not None:
                return float(timestamp_str)
            else:
                logger.warning(f"[SessionManager] Last active time not found for session {session_id}")
                return None
        except Exception as e:
            logger.error(f"[SessionManager] Failed to get last active time for session {session_id}: {e}")
            raise
    
    async def get_global_last_active_time(self) -> Optional[float]:
        """
        获取全局最后活动时间
        
        Returns:
            全局最后活动时间戳，如果不存在则返回None
        """
        try:
            timestamp_str = await self.redis_client.get(self.GLOBAL_LAST_ACTIVE_TIME_KEY)
            
            if timestamp_str is not None:
                return float(timestamp_str)
            else:
                logger.warning(f"[SessionManager] Global last active time not found")
                return None
        except Exception as e:
            logger.error(f"[SessionManager] Failed to get global last active time: {e}")
            raise
