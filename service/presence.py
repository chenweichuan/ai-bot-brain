"""
Presence Service - OpenAI-compatible single-round chat with memory injection.
Designed for Witron to reside in external agent environments (e.g. Cline).
No tool calling, no loop, no planning - pure thin proxy + memory + async save.
"""
import copy
from datetime import datetime
import json
import time
from typing import Optional, List, Dict, Any, AsyncGenerator, Union

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
        model: str = None,
        stream: bool = False,
        username: Optional[str] = None,
        **kwargs,
    ) -> Union[AsyncGenerator[Dict[str, Any], None], Dict[str, Any]]:
        """
        Single-round chat with memory: inject memory → forward to LLM → async memory save.
        This interface is specifically designed for third-party agent integration,
        providing a memory-enabled chat interface that can be directly called by external agents.
        Returns async generator of OpenAI SSE chunks (stream) or single dict (non-stream).
        """
        is_completions_format = "messages" in kwargs
        history = copy.deepcopy(kwargs["messages"]) if is_completions_format else copy.deepcopy(kwargs["input"])
        model = model if model and model != "default" else conf().get("agent_model")
        
        # Extract instructions
        instructions = ""
        if history[0].get("role") in ("system", "developer"):
            instructions = history[0]["content"]
            del history[0]

        # Prepare memory via LLM-judged recall
        memory = await self._recall_memory(
            history=slice_new_turn_messages(history) or history,
            instructions=instructions,
            username=username,
        )

        # Prepare context for LLM
        send_context = self._build_context(
            instructions=instructions,
            history=history,
            memory=memory,
            username=username,
        )

        request = dict(
            **kwargs,
            model=model,
            stream=stream,
        )
        if is_completions_format:
            request["messages"] = send_context
        else:
            request["input"] = send_context

        if stream:
            async def _stream_gen():
                new_history = []
                try:
                    async for chunk in await LlmClient.factory(request["model"]).chat(**request):
                        try:
                            if is_completions_format and chunk.get("choices"):
                                delta = chunk["choices"][0].get("delta", {})
                                if not new_history:
                                    new_history.append({"role": "assistant", "reasoning_content": "", "content": "", "tool_calls": []})
                                if delta.get("content"):
                                    new_history[0]["content"] += delta["content"]
                                if delta.get("reasoning_content"):
                                    new_history[0]["reasoning_content"] += delta["reasoning_content"]
                                if delta.get("tool_calls"):
                                    for tool_call in delta["tool_calls"]:
                                        if tool_call.get("id"):
                                            tool_call["function"]["arguments"] = tool_call["function"].get("arguments") or ""
                                            new_history[0]["tool_calls"].append(tool_call)
                                        else:
                                            new_history[0]["tool_calls"][-1]["function"]["arguments"] += tool_call["function"].get("arguments") or ""
                            elif chunk.get("response", {}).get("output"):
                                new_history = chunk["response"]["output"]
                        except Exception as e:
                            logger.error(f"[Presence] Stream chunk processing failed: {e}")
                        yield chunk
                finally:
                    # Async memory save after response completes (even on early disconnect / stream error)
                    try:
                        if new_history:
                            await self.impression_manager.enqueue_maintain(
                                history=slice_new_turn_messages(history + new_history),
                                instructions=instructions,
                                username=username,
                            )
                    except Exception as e:
                        logger.error(f"[Presence] Memory save failed: {e}")
            return _stream_gen()
        else:
            result = await LlmClient.factory(request["model"]).chat(**request)
            try:
                # Async memory save after response completes
                new_history = [result.get("choices")[0]["message"]] if is_completions_format else result.get("response", {}).get("output", [])
                if new_history:
                    await self.impression_manager.enqueue_maintain(
                        history=slice_new_turn_messages(history + new_history),
                        instructions=instructions,
                        username=username,
                    )
            except Exception as e:
                logger.error(f"[Presence] Memory save failed: {e}")
            return result

    async def _recall_memory(
        self,
        history: List[Dict[str, Any]],
        instructions: str = "",
        username: str = None,
    ) -> str:
        """
        Lightweight LLM-judged memory recall: classify current conversation topic
        against available categories/labels, then do targeted recall on top of
        recent mixed impressions (pinned + recent + category baseline).
        """
        model = conf().get("memory_model")

        impression_categories = await self.impression_manager.get_recent_categories()
        impression_labels = await self.impression_manager.get_mixed_labels()

        # Always get recent mixed impressions as baseline (includes pinned)
        mixed_impressions = await self.impression_manager.get_mixed_impressions()
        # mixed_impressions: List[(pin_emoji, (clue, content), score)], sorted desc
        mixed_clue_set = {clue for _, (clue, _), _ in mixed_impressions}

        # Get recall tool definition from global tool manager
        send_tools = await self.tool_manager.get_definitions(filter=[RecallImpressionsTool.name])

        # Build context
        send_messages = self.context_builder.build_context(
            history=[{
                "role": "user",
                "content": (
                    "All your memory impression categories:\n"
                    "------\n"
                    f"{', '.join([name for name, _ in reversed(impression_categories)] or [])}\n"
                    "------\n\n"
                    "All your memory impression labels:\n"
                    "------\n"
                    f"{', '.join([name for name, _ in reversed(impression_labels)] or [])}\n"
                    "------\n\n"
                    f"New turn of conversation{f' with {username}' if username else ''}:\n"
                    "------\n"
                    f"{json.dumps(history, ensure_ascii=False, indent=2)}\n"
                    "------\n\n"
                    "Note:\n"
                    f"- Call {RecallImpressionsTool.name} once if needed.\n"
                    "- If there's no need to recall anything, just reply \"RECENT\" to use recent mixed impressions only."
                )
            }],
            instructions=instructions,
        )
        
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
            recall_impressions = await self.impression_manager.get_impressions_by_clues(recall_clue_tuples)

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
        history: List[Dict[str, Any]],
        memory: str = "",
        username: str = None,
     ) -> List[Dict[str, Any]]:
        """Prepare context for LLM request"""
        history = copy.deepcopy(history)

        # Build system message
        system_message = self.context_builder.build_system_message(
            instructions=instructions
        )

        # Combine system message and messages
        history = [system_message] + history

        # Inject dynamic context as a synthetic system message
        # before the last user/assistant turn message (ephemeral, not persisted to history)
        last_turn_idx = len(history) - 1 - next(
            (i for i, msg in enumerate(reversed(history)) if msg["role"] in ["user", "assistant"]),
            len(history) - 1
        )
        history.insert(last_turn_idx, {
            "role": "system",
            "content": "\n\n".join(filter(None, [
                memory or "",
                "------",
                f"Now time is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "------",
                f"Current user: {username}" if username else "",
                "------" if username else "",
            ]))
        })

        return history
