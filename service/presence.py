"""
Presence Service - OpenAI-compatible single-round chat with memory injection.
Designed for Witron to reside in external agent environments (e.g. Cline).
No tool calling, no loop, no planning - pure thin proxy + memory + async save.
"""
import asyncio
from typing import Optional, List, Dict, Any, AsyncGenerator

from common.log import logger
from config import conf
from memory.impression_manager import ImpressionManager, slice_new_turn_messages
from memory.context_builder import ContextBuilder
from providers.llm.client import LlmClient


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

        self.memory_queue = asyncio.Queue(maxsize=100)
        self.is_processing_memory_queue = False

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = "",
        stream: bool = False,
        temperature: Optional[float] = None,
        username: Optional[str] = None,
        **_,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Single-round chat: inject memory → forward to LLM → async memory save.
        Returns async generator of OpenAI SSE chunks (stream) or single dict (non-stream).
        """
        model = model if model and model != "default" else conf().get("chat_model")
        # Prepare memory
        memory = await self.impression_manager.build_memory_context()

        # Extract instructions
        instructions = None
        if messages[0].get("role") in ("system", "developer"):
            instructions = messages[0]["content"]
            del messages[0]

        # Build system message
        system_message = self.context_builder.build_system_message(
            memory=memory,
            instructions=instructions,
        )

        # Prepare context for LLM
        messages = [system_message] + messages

        request = dict(model=model, messages=messages, stream=stream)
        if temperature is not None:
            request["temperature"] = temperature

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
                        await self._put_memory_queue(
                            username=username,
                            instructions=instructions,
                            history=slice_new_turn_messages(
                                messages + [{
                                    "role": "assistant",
                                    "content": reply_content
                                }]
                            ),
                        )
            return _stream_gen()
        else:
            result = await LlmClient.factory(request["model"]).chat(**request)
            reply_content = ""
            try:
                reply_content = result["choices"][0]["message"].get("content", "")
            except Exception:
                pass
            # Async memory save after response completes
            if reply_content:
                await self._put_memory_queue(
                    username=username,
                    instructions=instructions,
                    history=slice_new_turn_messages(
                        messages + [{
                            "role": "assistant",
                            "content": reply_content
                        }]
                    ),
                )
            return result

    async def _put_memory_queue(self, **task_params):
        """Add memory task to queue (same pattern as agent.py)"""
        try:
            if self.memory_queue.full():
                self.memory_queue.get_nowait()
                logger.warning("[Presence] Memory queue full, evicting oldest task")
            self.memory_queue.put_nowait(task_params)
            logger.info(f"[Presence] Added memory task, queue size: {self.memory_queue.qsize()}")
            if not self.is_processing_memory_queue:
                asyncio.create_task(self._process_memory_queue())
        except Exception as e:
            logger.error(f"[Presence] Memory queue error: {e}")

    async def _process_memory_queue(self):
        """Process memory tasks sequentially (same pattern as agent.py)"""
        if self.is_processing_memory_queue:
            return
        
        self.is_processing_memory_queue = True
        logger.info("[Presence] Started processing memory queue")
        
        try:
            while not self.memory_queue.empty():
                task_params = await self.memory_queue.get()
                try:
                    await self.impression_manager.maintain_impressions_by_llm(
                        username=task_params.get("username"),
                        instructions=task_params.get("instructions"),
                        messages=task_params["history"],
                    )
                    logger.info(f"[Presence] Memory task done, remaining: {self.memory_queue.qsize()}")
                except Exception as e:
                    logger.error(f"[Presence] Memory task failed: {e}")
                    logger.exception(e)
                finally:
                    # Mark task as done
                    self.memory_queue.task_done()
                    # Add small delay to avoid overwhelming the system
                    await asyncio.sleep(0.01)
        finally:
            self.is_processing_memory_queue = False
            logger.info("[Presence] Finished processing memory queue")