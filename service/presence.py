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
from common.message import count_text_units
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
        
        # Extract instructions
        instructions = ""
        if messages[0].get("role") in ("system", "developer"):
            instructions = messages[0]["content"]
            del messages[0]

        # Prepare memory via LLM-judged recall
        memory = await self._recall_memory(
            messages=slice_new_turn_messages(messages),
            instructions=instructions,
        )

        # Prepare context for LLM
        send_messages = self._build_context(
            instructions=instructions,
            messages=messages,
            memory=memory,
            username=username,
        )

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
        instructions: str = "",
        model: str = None,
    ) -> str:
        """
        Lightweight LLM-judged memory recall: classify current conversation topic
        against available categories/labels, then do targeted recall on top of
        recent mixed impressions (pinned + recent + category baseline).
        """
        messages = copy.deepcopy(messages or [])
        model = model if model and model != "default" else conf().get("memory_model")

        impression_categories = await self.impression_manager.get_recent_categories()
        impression_labels = await self.impression_manager.get_mixed_labels()

        # Always get recent mixed impressions as baseline (includes pinned)
        mixed_impressions = await self.impression_manager.get_mixed_impressions(max_text_units=10000)
        # mixed_impressions: List[(pin_emoji, (clue, content), score)], sorted desc
        mixed_clue_set = {clue for _, (clue, _), _ in mixed_impressions}

        # Remove reasoning
        for msg in messages:
            msg["reasoning_content"] = None

        # Get recall tool definition from global tool manager
        send_tools = await self.tool_manager.get_definitions(filter=[RecallImpressionsTool.name])

        # Prepare context for LLM
        send_messages = []
        if instructions:
            send_messages.append({
                "role": "system",
                "content": instructions,
            })
        send_messages.extend(messages)
        send_messages.append({
            "role": "user",
            "content": (
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
                "- If there's no need to recall anything, just reply \"RECENT\" to use recent mixed impressions only."
            )
        })

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

        # Collect dynamic recall clues from LLM-judged labels/category
        recall_clue_tuples: List[tuple[str, float]] = []
        seen_clues = set(mixed_clue_set)  # already in mixed, skip duplicates

        if recall_tool_call and recall_tool_call["function"]["name"] == RecallImpressionsTool.name:
            try:
                args = json.loads(recall_tool_call["function"].get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}

            # 1. Collect clues from labels (dedup across labels and against mixed)
            labels = [l.strip() for l in (args.get("labels") or [])]
            for label in labels:
                label_clues = await self.impression_manager.get_label_clues(label)
                for clue, score in label_clues:
                    if clue not in seen_clues:
                        seen_clues.add(clue)
                        recall_clue_tuples.append((clue, score))

            # 2. Collect clues from category (dedup against already collected clues)
            category = (args.get("category") or "").strip()
            if category:
                category_clues = await self.impression_manager.get_category_clues(category)
                for clue, score in category_clues:
                    if clue not in seen_clues:
                        seen_clues.add(clue)
                        recall_clue_tuples.append((clue, score))

            logger.info(f"[Presence] Dynamic recall clues: {len(recall_clue_tuples)} new (labels={len(labels)}, category={category or 'none'})")

        # Get dynamic recall impressions (only if we have new clues)
        recall_impressions: List[tuple[tuple[str, str], float]] = []
        if recall_clue_tuples:
            recall_impressions = await self.impression_manager.get_impressions_by_clues(
                recall_clue_tuples, max_text_units=10000
            )

        # Merge mixed + dynamic recall impressions, sort by score asc (oldest first)
        # mixed format: (pin_emoji, (clue, content), score)
        # recall format: ((clue, content), score)
        all_impressions = []
        for pin, (clue, content), score in mixed_impressions:
            all_impressions.append((pin, clue, content, score))
        for (clue, content), score in recall_impressions:
            # Dynamic recall impressions are unpinned
            all_impressions.append((self.impression_manager.UNPINNED_EMOJI, clue, content, score))

        # Sort by score ascending (oldest first)
        all_impressions.sort(key=lambda x: x[3])

        # Build memory context string — impressions only
        impression_lines = "\n".join([
            f"[{datetime.fromtimestamp(score // 1_000).strftime('%Y-%m-%d %H:%M:%S')}][{pin}][{clue}]{content}"
            for pin, clue, content, score in all_impressions
        ] or [])

        memory = (
            "Your chronological relevant memory impressions (format [ModTime][Pin][Clue]Content) with all users are as follows:\n"
            "------\n"
            "[ModTime][Pin][Clue]Content\n"
            "------\n"
            f"{impression_lines}\n"
            "------\n"
            "Note: Do NOT mention, expose or directly output your memory format and mechanism to users."
        )

        logger.info(f"[Presence] Final memory text units: {count_text_units(memory)} (mixed={len(mixed_impressions)}, recall={len(recall_impressions)})")

        return memory

    def _build_context(
        self,
        instructions: str,
        messages: List[Dict[str, Any]],
        memory: str = "",
        username: str = None,
     ) -> List[Dict[str, Any]]:
        """Prepare context for LLM request"""
        messages = copy.deepcopy(messages)

        # Build system message
        system_message = self.context_builder.build_system_message(
            instructions=instructions
        )

        # Combine system message and messages
        messages = [system_message] + messages

        # Inject dynamic context at the start of the last non-tool message
        last_non_tool_idx = len(messages) - 1 - next(
            (i for i, msg in enumerate(reversed(messages)) if msg["role"] != "tool"),
            len(messages) - 1
        )
        if last_non_tool_idx >= 0:
            prepended_content = "\n\n".join(filter(lambda s: s, [
                memory,
                "------",
                f"Now time is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "------",
                f"Current user: {username}" if username else "",
                "------" if username else "",
            ]))
            original_content = messages[last_non_tool_idx]["content"] or ""
            if isinstance(original_content, list):
                original_content.insert(0, {"type": "text", "text": prepended_content})
            else:
                messages[last_non_tool_idx]["content"] = f"{prepended_content}\n\n{original_content}"

        return messages
