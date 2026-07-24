"""Impression Entry Manager
- High-density symbol system, time-based rolling
- Fully loaded into system context during each conversation
"""
import json
import time
from typing import List, Dict, Any, Optional
from common.log import logger
from common.message import count_text_units, stringify_message
from config import conf
from providers.llm.client import LlmClient
from impressmem import ImpressMemConfig, ImpressMemManager, slice_new_turn_messages

from memory.context_builder import ContextBuilder


class ImpressionManager(ImpressMemManager):
    """Impression Entry Manager"""
    _instance: Optional['ImpressionManager'] = None
    
    MESSAGE_IDS_PER_SET: int = 500
    MESSAGE_TEXT_UNITS_PER_SET: int = 30000

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
        
        # Add extra key that ImpressMem doesn't have
        self.CLUE_MESSAGE_ID_ZSET_KEY = f"{self.KEY_PREFIX}:clue:message_ids:%s"

    # ==================== Clue Memory - Extra methods not in ImpressMem ====================

    async def get_clue_message_ids(self, clue: str, limit: int = MESSAGE_IDS_PER_SET, timestamp: Optional[float] = None) -> List[tuple[List[str], float]]:
        """
        Get limited message IDs for a given clue with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited message IDs sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.CLUE_MESSAGE_ID_ZSET_KEY % clue, timestamp, 0, start=0, num=limit, withscores=True)

    async def get_messages_by_clues(self, clues: List[str], max_text_units: int = MESSAGE_TEXT_UNITS_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited message sets for a given list of clues with their scores (timestamps)
        """
        from memory.session_manager import SessionManager
        
        session_manager = SessionManager.get_instance()
        
        combined_message_ids = []
        combined_message_id_set = set()
        selected_messages = []

        # Get message sets for each clue
        for clue in clues:
            message_ids = await self.get_clue_message_ids(clue, self.MESSAGE_IDS_PER_SET, timestamp)
            message_ids = list(filter(lambda x: x[0] not in combined_message_id_set, message_ids))
            combined_message_ids.extend(message_ids)
            combined_message_id_set.update([message_id[0] for message_id in message_ids])
        # Sort message sets by score (timestamp) in descending order
        combined_message_ids = sorted(combined_message_ids, key=lambda x: x[1], reverse=True)[:self.MESSAGE_IDS_PER_SET]
        
        # Get original messages for combined message IDs
        original_messages = await session_manager.multi_get_messages([message_id[0] for message_id in combined_message_ids])
        # Sort original messages by timestamp in descending order
        original_messages = sorted(original_messages, key=lambda x: x["timestamp"], reverse=True)
        
        # Accumulate until we reach max_text_units
        total_units = 0
        for message in original_messages:
            message_str = stringify_message(message)
            if message_str:
                units = count_text_units(message_str)
                if total_units + units > max_text_units:
                    break
                total_units += units
                selected_messages.append((message_str, message["timestamp"]))
        
        return selected_messages

    # ==================== Save Memory - Extra methods not in ImpressMem ====================

    async def save_clue_message_ids(self, clue: str, message_ids: List[str]) -> None:
        """
        Save a message IDs for a given clue with a timestamp score
        """
        clue = clue.strip().replace("\n", " ")
        message_ids = list(filter(lambda x: x, [message_id.strip().replace("\n", " ") for message_id in message_ids]))
        
        if not clue or not message_ids:
            logger.error(f"[ImpressionManager] Missing clue or message IDs")
            return
        
        # Use pipeline for atomic operations
        pipe = await self.redis_client.pipeline()
        
        for message_id in message_ids:
            # Use current timestamp as score
            score = time.time_ns() / 1_000_000
            # Save message IDs with score (timestamp)
            await pipe.zadd(self.CLUE_MESSAGE_ID_ZSET_KEY % clue, {message_id: score})
            
        # Remove old message IDs
        await pipe.zremrangebyrank(self.CLUE_MESSAGE_ID_ZSET_KEY % clue, 0, -10_001)
        
        # Execute pipeline
        await pipe.execute()
        
        logger.info(f"[ImpressionManager] Saved clue message IDs: [{clue}]{','.join(message_ids)}")

    # ==================== Organize Memory - Override to handle message IDs ====================

    async def merge_clues(self, from_clues: List[str], to_clue: str, new_content: str = "") -> Dict[str, Any]:
        """
        Merge multiple from_clues into to_clue, combining all related zsets using Redis native union operation
        
        Args:
            from_clues: List of source clues to merge
            to_clue: Target clue to merge into
            new_content: New content for the merged clue (if empty, will keep the content of to_clue)
        
        Returns:
            Dictionary with merge result information: new_content
        """
        # First call parent merge_clues
        result = await super().merge_clues(from_clues, to_clue, new_content)
        
        # Then handle message ID merging
        to_clue = to_clue.strip()
        from_clues = list(filter(
            lambda clue: clue and clue != to_clue,
            [clue.strip() for clue in from_clues]
        ))
        
        if not from_clues or not to_clue:
            return result
        
        # Get to_clue message ids zset keys
        to_message_ids_key = self.CLUE_MESSAGE_ID_ZSET_KEY % to_clue
        
        # Count messages before merge
        to_messages_before = await self.redis_client.zcard(to_message_ids_key)
        
        pipe = await self.redis_client.pipeline()
        
        # Merge message IDs
        for from_clue in from_clues:
            from_message_ids_key = self.CLUE_MESSAGE_ID_ZSET_KEY % from_clue
            
            # Union merge message IDs (take max score for duplicates)
            await pipe.zunionstore(
                to_message_ids_key,
                [from_message_ids_key, to_message_ids_key],
                aggregate="MAX"
            )
        
        # Count messages after merge
        await pipe.zcard(to_message_ids_key)
        
        # Execute pipeline
        results = await pipe.execute()
        to_messages_after = results[-1]
        
        # Calculate moved messages
        messages_moved = to_messages_after - to_messages_before
        
        logger.info(f"[ImpressionManager] Merged {len(from_clues)} clues into {to_clue}: moved {messages_moved} message IDs")
        
        # Add messages_moved to the result
        result["messages_moved"] = messages_moved
        return result

    # ==================== Maintain Impressions By LLM - Extra methods not in ImpressMem ====================

    async def maintain_impressions_by_llm(
        self,
        messages: List[Dict[str, Any]],
        username: str = None,
        instructions: str = "",
        model: str = conf().get("memory_model"),
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
        memory_context = await self.build_memory_context()

        # Get tool definitions directly from impressmem tools
        send_tools = self.get_maintain_tool_definitions()
        
        # Build context, limited messages
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
            "thinking": True,
            "stream": False,
            "temperature": 0.1,
            "tools": send_tools,
            "tool_choice": "auto"
        }
        response = await LlmClient.factory(request["model"]).chat(**request)
        
        # Get the response message
        maintenance_message = response["choices"][0]["message"]
        maintenance_tool_calls = maintenance_message.get("tool_calls") or []

        logger.info(f"[ImpressionManager] Tool calls for maintenance: {json.dumps(maintenance_tool_calls, ensure_ascii=False)}")
        
        # Execute each tool call
        await self.execute_maintain_tool_calls(maintenance_tool_calls)

        # Collect all save impression tool calls from memory_message and new turn-of-conversation messages
        all_save_impression_tool_calls = [
            tc for tc in maintenance_tool_calls if tc["function"]["name"] == self.save_impression_tool.name
        ]
        for message in messages:
            all_save_impression_tool_calls.extend([
                tc for tc in message.get("tool_calls") or []
                if tc["function"]["name"] == self.save_impression_tool.name
            ])

        # Save clue message IDs
        clue_message_ids = [msg["id"] for msg in messages if msg.get("id")]
        if clue_message_ids:
            for tool_call in all_save_impression_tool_calls:
                try:
                    arguments = tool_call["function"]["arguments"]
                    args = json.loads(arguments) if arguments else {}
                    clue: str = args.get("clue", "").strip()
                    await self.save_clue_message_ids(clue, clue_message_ids)
                except Exception as e:
                    logger.error(f"[ImpressionManager] Failed to save clue message IDs: {e}")
