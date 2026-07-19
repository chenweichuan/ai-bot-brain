#!/usr/bin/env python3
"""
Agent scheduler script that runs the agent's think method every minute with safety lock
"""
import asyncio
import time
import os
from typing import Optional

from config import conf

from common.log import logger
from common.redis import RedisClient
from memory.session_manager import SessionManager
from service.agent import AgentService


class InnerModeScheduler:
    """Auto scheduler for running agent think method with concurrency control"""
    _instance: Optional['InnerModeScheduler'] = None
    
    @classmethod
    def get_instance(cls):
        """Get singleton instance of AutoScheduler"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.redis_client = RedisClient.get_instance()
        self.session_manager = SessionManager.get_instance()
        self.agent_service = AgentService.get_instance()
        
        self.bot_name = conf().get("bot_name", "Bot")
        self.KEY_PREFIX = f"{self.bot_name.lower().replace(' ', '_')}:agent:auto"

        self.SESSION_ID_KEY = f"{self.KEY_PREFIX}:session_id"
        self.LOCK_KEY = f"{self.KEY_PREFIX}:running_lock"
        self.LOCK_TIMEOUT = 30  # Lock expires
        self.SCHEDULER_INTERVAL = conf().get("auto_scheduler_interval", 3600)

    async def _acquire_lock(self) -> bool:
        """Acquire distributed lock to prevent concurrent execution"""
        try:
            if not self.redis_client:
                logger.error("Redis client not initialized")
                return False
                
            # Set lock only if it doesn't exist, with expiration
            result = await self.redis_client.set(
                self.LOCK_KEY, 
                str(time.time()), 
                nx=True, 
                ex=self.LOCK_TIMEOUT
            )
            
            # 自动持续刷新过期时间机制
            if result is not None:
                asyncio.create_task(self._refresh_lock_expiration())
            
            return result is not None
        except Exception as e:
            logger.error(f"Failed to acquire lock: {e}")
            return False
            
    async def _refresh_lock_expiration(self):
        """Periodically refresh lock expiration"""
        while await self.redis_client.exists(self.LOCK_KEY):
            await asyncio.sleep(self.LOCK_TIMEOUT // 2)
            await self.redis_client.expire(self.LOCK_KEY, self.LOCK_TIMEOUT)
            
    async def _release_lock(self):
        """Release the distributed lock"""
        try:
            if not self.redis_client:
                logger.error("Redis client not initialized")
                return
                
            await self.redis_client.delete(self.LOCK_KEY)
            logger.info("Lock released successfully")
        except Exception as e:
            logger.error(f"Failed to release lock: {e}")
            
    async def _save_session_id(self, session_id: str):
        """Save session ID to Redis"""
        try:
            await self.redis_client.set(self.SESSION_ID_KEY, session_id)
            logger.info(f"Session_id saved to Redis: {session_id}")
        except Exception as e:
            logger.error(f"Failed to save session_id: {e}")

    async def _get_session_id(self) -> Optional[str]:
        """Get session ID from Redis"""
        try:
            session_id = await self.redis_client.get(self.SESSION_ID_KEY)
            if session_id:
                logger.info(f"Session_id loaded from Redis: {session_id}")
                return session_id.decode() if isinstance(session_id, bytes) else session_id
            return None
        except Exception as e:
            logger.error(f"Failed to load session_id: {e}")
            return None

    async def _delete_session_id(self):
        """Delete session ID from Redis"""
        try:
            await self.redis_client.delete(self.SESSION_ID_KEY)
            logger.info("Session_id deleted from Redis")
        except Exception as e:
            logger.error(f"Failed to delete session_id: {e}")

    async def run_agent_think(self):
        """Run the agent's think method with appropriate parameters"""
        logger.info("=== Starting agent autonomous thinking session ===")
        
        try:
            session_id = await self._get_session_id()
            
            # Create prompt telling the agent it's autonomous time
            instructions = f"""Now it's your own Inner Thinking Mode time. 
No user interaction is required and no user will see your inner thoughts.
You can:
- Review your memory impressions to see if there's anything that needs follow-up or completion.
- Check for new messages from all sources to see if there's anything that need to reply.
- Think about any other things you can do and planning future tasks."""

            async for chunk in self.agent_service.think(
                session_id=session_id,
                instructions=instructions,
                model=conf().get("chat_model"),
            ):
                # Capture and save session_id
                if "session_id" in chunk:
                    session_id = chunk["session_id"]
                    await self._save_session_id(session_id)

            await self._delete_session_id()
            
            logger.info("=== Agent autonomous thinking session completed ===")
            
        except Exception as e:
            logger.error(f"Error running agent think: {e}")
            logger.exception(e)
            
    async def start_scheduler(self):
        """Main task that runs with safety check"""
        # Check if we can acquire the lock
        if not await self._acquire_lock():
            logger.info("Previous execution is still running, skipping this iteration")
            return

        try:
            # Run the agent think method
            await self.run_agent_think()
        finally:
            # Release the lock
            await self._release_lock()
            
    @classmethod
    def setup(cls):
        """Setup AutoScheduler"""
        _instance = cls.get_instance()
        
        async def master_scheduler():
            while True:
                try:
                    asyncio.create_task(_instance.start_scheduler())
                except Exception as e:
                    logger.error(f"Error in master scheduler: {e}")
                    logger.exception(e)
                finally:
                    await asyncio.sleep(_instance.SCHEDULER_INTERVAL)

        asyncio.create_task(master_scheduler())
