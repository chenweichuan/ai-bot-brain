"""Impression Entry Manager
- High-density symbol system, time-based rolling
- Fully loaded into system context during each conversation
"""
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
            impression_text_units_per_set=15000,
            unpinned_emoji="⚪",
            pinned_emoji="📌",
        )
        
        # Call parent __init__ but we'll override redis_client
        super().__init__(config)
        
        self.context_builder = ContextBuilder.get_instance()

    # ==================== Maintain Impressions By LLM ====================

    async def maintain_impressions_by_llm(
        self,
        messages: List[Dict[str, Any]],
        username: str = None,
        instructions: str = "",
        model: str = None,
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
        model = model if model and model != "default" else conf().get("memory_model")
        memory_context = await self.build_memory_context()

        # Get tool definitions directly from impressmem tools
        send_tools = self.get_maintain_tool_definitions()
        
        # Build context
        send_messages = self.context_builder.build_context(
            history=messages,
            memory=memory_context,
            instructions=instructions,
            tools=send_tools,
        )
        
        # If the last message is system or user, return
        if send_messages[-1]["role"] in ["system", "user"]:
            return
        
        send_messages.append({
            "role": "user",
            "content": f"New turn of conversation{f' with {username}' if username else ''}.\n"
                + self.get_maintain_prompt(),
        })
        
        request = {
            "messages": send_messages,
            "model": model,
            "thinking": False,
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
