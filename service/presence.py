"""
Presence Service - OpenAI-compatible single-round chat with memory injection.
Designed for Witron to reside in external agent environments (e.g. Cline).
No tool calling, no loop, no planning - pure thin proxy + memory + async save.
"""
import copy
from datetime import datetime
import json
import time
from typing import Optional, List, Dict, Any, AsyncGenerator

from common.log import logger
from config import conf
from memory.impression_manager import ImpressionManager, slice_new_turn_messages
from memory.context_builder import ContextBuilder
from providers.llm.client import LlmClient
from tools.manager import ToolManager
from tools.memory import RecallImpressionsTool


class PresenceService:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.impression_manager = ImpressionManager.get_instance()
        self.context_builder = ContextBuilder.get_instance()
        self.tool_manager = ToolManager.get_instance()

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        stream: bool = False,
        username: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Single-round chat: inject memory → forward to LLM → async memory save.
        Returns async generator of OpenAI SSE chunks (stream) or single dict (non-stream).
        """
        model = model if model and model != "default" else conf().get("chat_model")

        # Prepare memory via LLM-judged recall
        memory = await self._recall_memory(slice_new_turn_messages(messages))

        # Extract instructions
        instructions = f"Current user: {username}" if username else ""
        if messages[0].get("role") in ("system", "developer"):
            instructions += "\n\n" if instructions else ""
            instructions += messages[0]["content"]
            del messages[0]

        # Build system message
        system_message = self.context_builder.build_system_message(
            memory=memory,
            instructions=instructions,
        )

        # Prepare context for LLM
        send_messages = [system_message] + messages

        request = dict(
            **kwargs,
            messages=send_messages,
            model=model,
            stream=stream,
        )

        if stream:
            async def _stream_gen():
                reply_content = ""
                try:
                    async for chunk in await LlmClient.factory(request["model"]).chat(**request):
                        if chunk.get("choices"):
                            delta = chunk["choices"][0].get("delta", {})
                            if delta.get("content"):
                                reply_content += delta["content"]
                        yield chunk
                finally:
                    # Async memory save after response completes
                    if reply_content:
                        await self.impression_manager.enqueue_maintain(
                            messages=slice_new_turn_messages(
                                messages + [{
                                    "role": "assistant",
                                    "content": reply_content
                                }]
                            ),
                            instructions=instructions,
                            username=username,
                        )
            return _stream_gen()
        else:
            result = await LlmClient.factory(request["model"]).chat(**request)
            reply_content = ""
            if result.get("choices"):
                message = result["choices"][0].get("message", {})
                reply_content = message.get("content")
            # Async memory save after response completes
            if reply_content:
                await self.impression_manager.enqueue_maintain(
                    messages=slice_new_turn_messages(
                        messages + [{
                            "role": "assistant",
                            "content": reply_content
                        }]
                    ),
                    instructions=instructions,
                    username=username,
                )
            return result

    async def _recall_memory(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
    ) -> str:
        """
        Lightweight LLM-judged memory recall: classify current conversation topic
        against available categories/labels, then either do targeted recall or
        fall back to recent mixed impressions.
        """
        messages = copy.deepcopy(messages or [])
        model = model if model and model != "default" else conf().get("memory_model")

        impression_categories = await self.impression_manager.get_recent_categories()
        impression_labels = await self.impression_manager.get_mixed_labels()

        # Remove reasoning
        for msg in messages:
            msg["reasoning_content"] = None

        # Get recall tool definition from global tool manager
        send_tools = await self.tool_manager.get_definitions(filter=[RecallImpressionsTool.name])

        send_messages = messages + [{
            "role": "user",
            "content":
                "Memory impression categories:\n"
                "------\n"
                f"{', '.join([name for name, _ in reversed(impression_categories)] or [])}\n"
                "------\n\n"
                "Memory impression labels:\n"
                "------\n"
                f"{', '.join([name for name, _ in reversed(impression_labels)] or [])}\n"
                "------\n\n"
                "Note:\n"
                f"- Call {RecallImpressionsTool.name} once if needed.\n"
                "- If there's no need to recall anything, just reply \"RECENT\" to get recent mixed impressions."
        }]

        request = {
            "messages": send_messages,
            "model": model,
            "thinking": False,
            "stream": False,
            "temperature": 0.2,
            "tools": send_tools,
            "tool_choice": "auto"
        }
        recall_start = time.time()
        response = await LlmClient.factory(request["model"]).chat(**request)
        recall_latency = round(time.time() - recall_start, 2)

        recall_message = response["choices"][0]["message"]
        recall_tool_call = recall_message["tool_calls"][0] if recall_message.get("tool_calls") else None

        logger.info(f"[Presence] Recall judge ({recall_latency}s): {json.dumps(recall_tool_call, ensure_ascii=False)}")

        if recall_tool_call and recall_tool_call["function"]["name"] == RecallImpressionsTool.name:
            pinned_impressions = await self.impression_manager.get_impressions_by_clues(
                await self.impression_manager.get_pinned_clues(),
                self.impression_manager.IMPRESSION_TEXT_UNITS_PER_SET // 5,
            )
            recall_result = await self.tool_manager.execute(recall_tool_call)
            memory = (
                "Recent pinned memory impressions (format [ModTime][Clue]Content):\n"
                + "\n".join([
                    f"[{datetime.fromtimestamp(score // 1_000).strftime('%Y-%m-%d %H:%M:%S')}][{clue}]{content}"
                    for (clue, content), score in pinned_impressions
                ] or [])
                + "\n\n"
                + recall_result['content']
            )
        else:
            memory = await self.impression_manager.build_memory_context()

        return memory

