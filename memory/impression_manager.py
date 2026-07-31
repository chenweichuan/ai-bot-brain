"""Impression Entry Manager
- High-density symbol system, time-based rolling
- Fully loaded into system context during each conversation
"""
import asyncio
import copy
import json
import time
from typing import List, Dict, Any, Optional
from common.log import logger
from config import conf
from providers.llm.client import LlmClient
from impressmem import (
    ImpressMemConfig,
    ImpressMemManager,
    slice_new_turn_messages
)
from impressmem.tools import (
    SaveImpressionsTool,
    OrganizeImpressionsTool,
    RecallImpressionsTool,
)

from memory.context_builder import ContextBuilder

# Re-export for internal module convenience
__all__ = [
    "ImpressionManager",
    "slice_new_turn_messages",
    "SaveImpressionsTool",
    "OrganizeImpressionsTool",
    "RecallImpressionsTool",
]


class ImpressionManager(ImpressMemManager):
    """Impression Entry Manager"""
    _instance: Optional['ImpressionManager'] = None

    @classmethod
    def get_instance(cls) -> 'ImpressionManager':
        """
        Get singleton instance of ImpressionManager
        
        Returns:
            ImpressionManager instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # Create config with project settings
        config = ImpressMemConfig(
            bot_name=conf().get("bot_name", "Bot"),
            redis_config=conf().get("redis", {}),
            categories_per_set=500,
            labels_per_set=1500,
            clues_per_set=500,
            impression_text_units_per_set=10000,
            unpinned_emoji="⚪",
            pinned_emoji="📌",
        )
        
        # Call parent __init__ but we'll override redis_client
        super().__init__(config)
        
        self.context_builder = ContextBuilder.get_instance()

        # Async memory maintenance queue
        self._maintain_queue = asyncio.Queue(maxsize=100)
        self._is_processing_maintain_queue = False

    # ==================== Maintain Impressions By LLM ====================

    async def maintain_impressions_by_llm(
        self,
        messages: List[Dict[str, Any]],
        instructions: str = "",
        model: str = None,
        username: str = None,
    ) -> None:
        """
        Save impression entries based on the messages

        Args:
            messages: Conversation messages
            model: LLM model name to use
            instructions: Instructions for the LLM
            username: Username of the user
        """
        # Make LLM request to save or organize impressions using impressmem's tools directly
        messages = copy.deepcopy(messages or [])
        model = model if model and model != "default" else conf().get("memory_model")
        memory = await self.build_memory_context()

        # Remove reasoning
        for msg in messages:
            msg["reasoning_content"] = None

        # Append maintain instructions
        messages.append({
            "role": "user",
            "content": f"New turn of conversation{f' with {username}' if username else ''}.\n"
                + self.get_maintain_prompt(),
        })
        
        # Get tool definitions directly from impressmem tools
        send_tools = self.get_maintain_tool_definitions()
        
        # Build context
        send_messages = self.context_builder.build_context(
            history=messages,
            memory=memory,
            instructions=instructions,
            tools=send_tools,
        )
        
        # Return if there is no assistant message
        if not any(msg["role"] == "assistant" for msg in send_messages):
            return
        
        request = {
            "messages": send_messages,
            "model": model,
            "thinking": True,
            "stream": False,
            "temperature": 0.1,
            "tools": send_tools,
            "tool_choice": "auto"
        }
        llm_start = time.monotonic()
        response = await LlmClient.factory(request["model"]).chat(**request)
        llm_elapsed = time.monotonic() - llm_start

        # Get the response message
        maintenance_message = response["choices"][0]["message"]
        maintenance_tool_calls = maintenance_message.get("tool_calls") or []

        logger.info(f"[ImpressionManager] Tool calls for maintenance (LLM {llm_elapsed:.2f}s): {json.dumps(maintenance_tool_calls, ensure_ascii=False)}")
        
        # Execute each tool call
        await self.execute_maintain_tool_calls(maintenance_tool_calls)

    async def enqueue_maintain(
        self,
        messages: List[Dict[str, Any]],
        instructions: str = "",
        username: str = None,
    ) -> None:
        """
        Enqueue a memory maintenance task to be processed asynchronously.
        Non-blocking: returns immediately after putting task into queue.

        Args:
            messages: Conversation messages to maintain from
            username: Username of the user
            instructions: Instructions for the LLM
        """
        try:
            if self._maintain_queue.full():
                # Remove the oldest item to make space
                self._maintain_queue.get_nowait()
                logger.warning("[ImpressionManager] Memory queue is full. Evicting oldest task to make space.")
            self._maintain_queue.put_nowait({
                "messages": messages,
                "instructions": instructions,
                "username": username,
            })
            logger.info(f"[ImpressionManager] Added memory task to queue. Queue size: {self._maintain_queue.qsize()}")
            # Start processing queue if not already processing
            if not self._is_processing_maintain_queue:
                asyncio.create_task(self._process_maintain_queue())
        except Exception as e:
            logger.error(f"[ImpressionManager] Enqueue memory task error: {e}")

    async def _process_maintain_queue(self) -> None:
        """Process memory maintenance tasks from queue sequentially"""
        if self._is_processing_maintain_queue:
            return
        self._is_processing_maintain_queue = True

        try:
            logger.info("[ImpressionManager] Started processing memory queue")

            while not self._maintain_queue.empty():
                task = await self._maintain_queue.get()
                try:
                    await self.maintain_impressions_by_llm(
                        messages=task["messages"],
                        instructions=task.get("instructions", ""),
                        username=task.get("username"),
                    )
                    logger.info(f"[ImpressionManager] Completed memory task, remaining in queue: {self._maintain_queue.qsize()}")
                except Exception as e:
                    logger.error(f"[ImpressionManager] Failed to process memory task: {e}")
                    logger.exception(e)
                finally:
                    # Mark task as done
                    self._maintain_queue.task_done()
                    # Add small delay to avoid overwhelming the system
                    await asyncio.sleep(0.01)
        finally:
            self._is_processing_maintain_queue = False
            logger.info("[ImpressionManager] Finished processing memory queue")
