"""
Agent for handling LLM requests with tools calling and looping capability
"""
import asyncio
import json
import re
import time
from typing import Dict, List, Any, AsyncGenerator, Optional

from common.log import logger
from common.message import count_text_units, count_messages_text_units
from common.redis import RedisClient
from config import conf
from memory.context_builder import ContextBuilder
from memory.impression_manager import ImpressionManager, slice_new_turn_messages
from memory.session_manager import SessionManager
from providers.llm.client import LlmClient
from tools.manager import ToolManager
from tools.flowcontrol import FlowWaitForInputTool, FlowCompleteTool


class AgentService:
    """Agent that handles LLM requests with tools calling and looping capability"""
    _instance = None

    MAX_THINK_ROUNDS = conf().get("max_think_rounds", 100)
    HISTORY_REDUCTION_RATIO = 0.7

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.impression_manager = ImpressionManager.get_instance()
        self.session_manager = SessionManager.get_instance()
        self.context_builder = ContextBuilder.get_instance()
        self.tool_manager = ToolManager.get_instance()
        self.redis_client = RedisClient.get_instance()

        self.bot_name = conf().get("bot_name", "Bot")
        self.KEY_PREFIX = f"{self.bot_name.lower().replace(' ', '_')}:agent"
        self.HISTORY_STARTING_MESSAGE_ID_KEY = f"{self.KEY_PREFIX}:history_starting_message_id:%s"
        
        # Client action waiters: {session_id: asyncio.Event}
        self.client_action_waiters: Dict[str, asyncio.Event] = {}
        
        # Client tool waiters: {session_id: {tool_call_id: result}}
        self.client_tool_waiters: Dict[str, Dict[str, Dict]] = {}
    
    async def think(
        self,
        username: str = None,
        session_id: str = None,
        messages: List[Dict[str, Any]] = None,
        **kargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Main thinking loop that handles multi-round iteration"""
        messages = messages or []
        current_round = 0

        # Prepare session
        session_id = await self._prepare_session(username, session_id)
        yield { "session_id": session_id }

        # Save new messages
        if messages:
            new_msg_ids = await self._save_new_messages(username, session_id, messages)
            yield { "message_ids": new_msg_ids }

        while True:
            # Set session active time of new round
            active_time = await self._set_active_time(session_id)

            # Run new round
            async for chunk in self._think_round(
                username=username,
                session_id=session_id,
                current_round=current_round,
                active_time=active_time,
                **kargs
            ):
                yield chunk
                # Determine whether to exit
                if "finish_reason" in chunk:
                    return

            # Handling active time check
            if not await self._check_active_time(session_id, active_time):
                logger.info(f"[Agent] Think session {session_id} has been replaced by new request, exiting")
                yield { "finish_reason": "stop" }
                return

            # Handling iterative rounds
            current_round += 1
            if current_round >= self.MAX_THINK_ROUNDS:
                logger.info(f"[Agent] Think {session_id} has reached the max rounds {self.MAX_THINK_ROUNDS}")
                yield { "finish_reason": "rounds" }
                return

            # Add small delay to avoid overwhelming the system
            await asyncio.sleep(0.01)

    async def receive_message(
        self,
        username: str,
        message: Dict[str, Any],
        session_id: str = None,
    ) -> Dict[str, Any]:
        """
        保存消息到会话
        """
        # Prepare session
        session_id = await self._prepare_session(username, session_id)

        # Set active time
        await self._set_active_time(session_id)

        # Save new messages
        new_msg_ids = await self._save_new_messages(username, session_id, [message])

        return { "session_id": session_id, "message_id": new_msg_ids[0] }

    async def end_client_wait_action(
        self,
        username: str,
        session_id: str,
    ):
        """
        客户端通知 wait action 结束
        """
        # Validate session permission
        await self.session_manager.check_user_session(username, session_id)
        
        event = self.client_action_waiters.setdefault(session_id, asyncio.Event())
        if not event.is_set():
            event.set()

        logger.info(f"[Agent] End wait action for session {session_id}")

    async def receive_client_tool_result(
        self,
        username: str,
        session_id: str,
        tool_call_id: str,
        content: str,
        summary: str,
    ):
        """
        提交 client tool 执行结果
        """
        # Validate session permission
        await self.session_manager.check_user_session(username, session_id)
        
        waiter = self.client_tool_waiters.setdefault(session_id, {})
        waiter[tool_call_id] = {
            "content": content,
            "summary": summary,
        }

        logger.info(f"[Agent] Received client tool result for {tool_call_id}")

    async def get_history(
        self,
        username: str,
        session_id: str,
        from_message_id: str = None,
        after_message_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        获取会话消息历史
        """
        valid_fields = [
            "id", "timestamp", "mod_time", 
            "role", "reasoning_content", "content",
            "name", "tool_calls", "tool_call_id", "model",
        ]
        
        await self.session_manager.check_user_session(username, session_id)
        history = await self.session_manager.get_message_history(
            session_id=session_id,
            from_message_id=from_message_id,
            after_message_id=after_message_id,
        )
        
        valid_history = []
        for msg in history:
            # remove tool call arguments
            if msg.get("tool_calls"):
                for tool_call in msg["tool_calls"]:
                    tool_call["function"]["arguments"] = ""
            # replace tool message content with summary
            if msg.get("role") == "tool":
                msg["content"] = msg.get("summary", "")
            # filter valid fields
            msg = {k: v for k, v in msg.items() if k in valid_fields}
            # filter out empty messages
            if msg.get("reasoning_content") or msg.get("content") or msg.get("tool_calls"):
                valid_history.append(msg)

        return valid_history

    async def _think_round(
        self,
        username: str = None,
        session_id: str = None,
        model: str = None,
        instructions: str = "",
        actions: List[Dict[str, str]] = None,
        tools: List[Dict[str, Any]] = None,
        thinking: bool = True,
        temperature: float = 0.2,
        max_text_units: int = ContextBuilder.MAX_TEXT_UNITS,
        max_messages: int = ContextBuilder.MAX_MESSAGES,
        max_model_rounds: int = ContextBuilder.MAX_MODEL_ROUNDS,
        current_round: int = 0,
        active_time: Optional[float] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Single thinking round that handles LLM call and action/tool processing"""
        model = model if model and model != "default" else conf().get("chat_model")
        actions = actions or []
        tools = tools or []

        if not await self._check_active_time(session_id, active_time):
            return

        # Prepare memory
        memory = await self.impression_manager.build_memory_context()

        # Read history start marker from Redis
        from_message_id = await self._get_history_starting_message_id(session_id)

        # Fetch history starting from marker
        history = await self.session_manager.get_message_history(
            session_id=session_id,
            from_message_id=from_message_id,
            limit=ContextBuilder.MAX_MESSAGES,
        )

        # Prepare actions for LLM - prepend wait action when client actions are present
        if actions:
            wait_action = {
                "name": "wait",
                "description": "Wait for client to signal continue before proceeding with subsequent output.",
            }
            send_actions = [wait_action] + actions
        else:
            send_actions = []

        # Prepare tools for LLM
        host_tools = await self.tool_manager.get_definitions()
        client_tools = tools
        send_tools = host_tools + client_tools

        # Prepare context for LLM
        send_messages = self.context_builder.build_context(
            history=history,
            memory=memory,
            instructions=instructions,
            actions=send_actions,
            tools=send_tools,
            max_text_units=max_text_units,
            max_messages=max_messages,
            max_model_rounds=max_model_rounds,
        )

        logger.info(f"[Agent] Start thinking, round: {current_round}, session_id: {session_id}, active_time: {active_time}")

        # Create bot message placeholder
        bot_message = self.session_manager.create_message({
            "role": "assistant",
            "reasoning_content": "",
            "content": "",
            "tool_calls": None,
            "to_name": username,
            "model": None,
            "streaming": True,
        })
        llm_finish_reason = None

        # Add bot message placeholder to history
        history.append(bot_message)
        yield bot_message

        # Make LLM request
        try:
            request = {
                "messages": send_messages,
                "model": model,
                "thinking": thinking != False,
                "stream": True,
                "tools": send_tools if send_tools else None,
                "tool_choice": "auto" if send_tools else None,
                "temperature": temperature,
            }
            response = await LlmClient.factory(request["model"]).chat(**request)

            # Action processing position
            last_action_end_pos = 0
            
            # Streaming response
            async for chunk in response:
                if not await self._check_active_time(session_id, active_time):
                    return
                
                if not chunk.get("choices"):
                    continue

                choice = chunk["choices"][0]
                delta = choice.get("delta", {})

                # Send actual model name at the first chunk
                if not bot_message.get("model"):
                    bot_message["model"] = chunk.get("model") or model
                    yield { "model": bot_message["model"] }
                
                # Handle reasoning_content
                if delta.get("reasoning_content"):
                    bot_message["reasoning_content"] += delta["reasoning_content"]
                    yield { "reasoning_content": delta["reasoning_content"] }
                
                # Handle content
                if delta.get("content"):
                    bot_message["content"] += delta["content"]
                    yield { "content": delta["content"] }

                    # Detect next action call
                    if actions:
                        async for item in self._detect_action_call(
                            content=bot_message["content"],
                            last_end_pos=last_action_end_pos,
                            session_id=session_id,
                            current_round=current_round,
                        ):
                            if "action_call" in item:
                                yield item
                            elif "end_pos" in item:
                                last_action_end_pos = item["end_pos"]
                
                # Handle tool calls
                for tool_call in delta.get("tool_calls") or []:
                    bot_message["tool_calls"] = bot_message["tool_calls"] or []
                    if tool_call.get("id"):
                        tool_call = { "id": tool_call["id"], "type": tool_call["type"], "function": tool_call["function"] }
                        tool_call["function"]["arguments"] = tool_call["function"].get("arguments") or ""
                        bot_message["tool_calls"].append(tool_call)
                        yield { "tool_calls": [tool_call] }
                    else:
                        bot_message["tool_calls"][-1]["function"]["arguments"] += tool_call["function"]["arguments"]
 
                # Handle finish reason
                if choice.get("finish_reason"):
                    llm_finish_reason = choice["finish_reason"]
 
                # Save message to session manager
                await self.session_manager.save_message(session_id, bot_message)
 
            logger.info(
                f"[Agent] Finish thinking step, round: {current_round}, "
                f"message: {json.dumps(bot_message, ensure_ascii=False)}"
            )

            # Deal with tool calls
            if bot_message.get("tool_calls"):
                async for chunk in self._execute_tool_calls(
                    tool_calls=bot_message["tool_calls"],
                    host_tools=host_tools,
                    session_id=session_id,
                    active_time=active_time,
                    history=history,
                    current_round=current_round,
                ):
                    yield chunk
        except Exception as e:
            logger.error(f"[Agent] Think error: {e}")
            logger.exception(e)
            yield { "content": f"```\n{str(e)}\n```" }
            yield { "finish_reason": "error" }
            return
        finally:
            bot_message["streaming"] = False
            await self.session_manager.save_message(session_id, bot_message)
            # Update history range marker based on three MAX thresholds (async, non-blocking)
            asyncio.create_task(self._update_history_marker(
                session_id=session_id,
                send_messages=send_messages,
                history=history,
                max_text_units=max_text_units,
                max_messages=max_messages,
                max_model_rounds=max_model_rounds,
            ))
            # If bot_message has content or tool_calls, enqueue memory maintenance
            if bot_message.get("content") or bot_message.get("tool_calls"):
                await self.impression_manager.enqueue_maintain(
                    messages=slice_new_turn_messages(history),
                    instructions=instructions,
                    username=username,
                )

        # Check if we should continue thinking
        should_continue = False
        if bot_message["tool_calls"]:
            # If there are tool calls (except wait input and complete), we should continue thinking
            tool_call_names = [t["function"]["name"] for t in bot_message["tool_calls"] or []]
            should_continue = FlowWaitForInputTool.name not in tool_call_names \
                and FlowCompleteTool.name not in tool_call_names
        elif llm_finish_reason != "stop" and bot_message.get("content"):
            # If LLM did not stop and there is content, it may indicate that the output has not ended yet.
            should_continue = True
        # Complete
        if not should_continue:
            # Remove client action waiter
            if self.client_action_waiters.get(session_id):
                del self.client_action_waiters[session_id]
            # Remove client tool waiter
            if self.client_tool_waiters.get(session_id):
                del self.client_tool_waiters[session_id]
            logger.info(f"[Agent] Think {session_id} is complete")
            yield { "finish_reason": "complete" }

    async def _prepare_session(self, username: str, session_id: str) -> str:
        """Prepare session for agent"""
        if not session_id:
            # Create new session
            session_id = self.session_manager.generate_session_id()
            # Save user session
            if username:
                await self.session_manager.save_user_session(username, session_id)

        # Check if session belongs to user
        if username:
            await self.session_manager.check_user_session(username, session_id)
            
        return session_id

    async def _set_active_time(self, session_id: str) -> float:
        """Set active_time in Redis """
        return await self.session_manager.set_session_last_active_time(session_id)
    
    async def _check_active_time(self, session_id: str, active_time: float) -> bool:
        """Check if active_time is still the same"""
        stored_active_time = await self.session_manager.get_session_last_active_time(session_id)
        result = stored_active_time == active_time if stored_active_time else False
        return result
    
    async def _save_new_messages(self, username: str, session_id: str, messages: List[Dict[str, Any]]):
        """Save new messages to session"""
        new_msg_ids = []
        
        valid_new_msg_fields = ["role", "content", "tool_call_id"]
        for msg in messages or []:
            # Truncate content if it exceeds the maximum number of text units
            max_content_units = 30000
            if isinstance(msg["content"], str) and count_text_units(msg["content"]) > max_content_units:
                truncated_length = len(msg["content"]) / count_text_units(msg["content"]) * max_content_units * 0.9
                msg["content"] = f"{msg['content'][:int(truncated_length/2)]}\n...[Content Truncated]...\n{msg['content'][-int(truncated_length/2):]}"
            elif isinstance(msg["content"], list):
                for part in msg["content"]:
                    if part["type"] == "text" and count_text_units(part["text"]) > max_content_units:
                        truncated_length = len(part["text"]) / count_text_units(part["text"]) * max_content_units * 0.9
                        part["text"] = f"{part['text'][:int(truncated_length/2)]}\n...[Content Truncated]...\n{part['text'][-int(truncated_length/2):]}"

            msg = self.session_manager.create_message({
                k: v for k, v in msg.items() if k in valid_new_msg_fields
            })
            msg["name"] = username

            await self.session_manager.save_message(session_id, msg)
            new_msg_ids.append(msg["id"])
    
        return new_msg_ids

    async def _get_history_starting_message_id(self, session_id: str) -> Optional[str]:
        """从Redis读取会话history读取起始message_id标记"""
        key = self.HISTORY_STARTING_MESSAGE_ID_KEY % session_id
        return await self.redis_client.get(key)

    async def _set_history_starting_message_id(self, session_id: str, message_id: str):
        """将会话history读取起始message_id标记写入Redis"""
        key = self.HISTORY_STARTING_MESSAGE_ID_KEY % session_id
        await self.redis_client.set(key, message_id)
        logger.info(f"[Agent] Set history from_message_id for session {session_id}: {message_id}")

    async def _update_history_marker(
        self,
        session_id: str,
        send_messages: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        max_text_units: int,
        max_messages: int,
        max_model_rounds: int,
    ):
        """
        根据三个MAX维度判断history读取范围缩减策略，取最激进的截断点更新Redis标记：
        1. send_messages条数 >= MAX_MESSAGES → 缩减到最新70%*MAX条数消息
        2. assistant条数 >= MAX_MODEL_ROUNDS → 缩减到最新70%*MAX条assistant消息
        3. send_messages+bot_message总text_units >= MAX_TEXT_UNITS → 缩减到最新70%*MAX - system units

        send_messages[0]为system消息，send_messages[1:]对应过滤后的history，
        通过偏移量 (send_messages_idx - 1) 近似映射回原始history获取message_id。
        30%的缩减余量足以容纳filter_history造成的偏移误差。
        """
        try:
            # Append new round message(s) to send_messages
            last_history_assistant_idx = len(history) - 1 - next((i for i, msg in enumerate(reversed(history)) if msg["role"] == "assistant"), 0)
            send_messages += history[last_history_assistant_idx:]
            
            ratio = self.HISTORY_REDUCTION_RATIO
            candidate_boundary_idx = -1  # boundary index in send_messages

            # Check 1: MAX_MESSAGES - total message count
            if len(send_messages) >= max_messages:
                keep_count = int(max_messages * ratio)
                boundary_idx = len(send_messages) - keep_count
                candidate_boundary_idx = max(candidate_boundary_idx, boundary_idx)
                logger.info(
                    f"[Agent] MAX_MESSAGES triggered: {len(send_messages)} >= {max_messages}, "
                    f"keep latest {keep_count}"
                )

            # Check 2: MAX_MODEL_ROUNDS - assistant message count
            assistant_idxes = [
                i for i, msg in enumerate(send_messages)
                if msg.get("role") == "assistant"
            ]
            if len(assistant_idxes) >= max_model_rounds:
                keep_count = int(max_model_rounds * ratio)
                boundary_idx = assistant_idxes[-keep_count]
                candidate_boundary_idx = max(candidate_boundary_idx, boundary_idx)
                logger.info(
                    f"[Agent] MAX_MODEL_ROUNDS triggered: {len(assistant_idxes)} >= {max_model_rounds}, "
                    f"keep latest {keep_count} assistant messages"
                )

            # Check 3: MAX_TEXT_UNITS - total text units
            total_units = count_messages_text_units(send_messages)
            if total_units >= max_text_units:
                system_units = count_messages_text_units([send_messages[0]])
                target_units = int(max_text_units * ratio) - system_units

                accumulated = 0
                # Iterate backward, skip system message at index 0
                for i in range(len(send_messages) - 1, 0, -1):
                    msg_units = count_messages_text_units([send_messages[i]])
                    if accumulated + msg_units > target_units:
                        candidate_boundary_idx = max(candidate_boundary_idx, i)
                        logger.info(
                            f"[Agent] MAX_TEXT_UNITS triggered: {total_units} >= {max_text_units}, "
                            f"target units: {target_units}, boundary at send_messages index {i}"
                        )
                        break
                    accumulated += msg_units

            # Map send_messages boundary index to original history via backward offset from tail
            if candidate_boundary_idx >= 1:
                history_backword_idx = len(send_messages) - candidate_boundary_idx
                if 1 <= history_backword_idx <= len(history):
                    message_id = history[-history_backword_idx].get("id")
                    if message_id:
                        await self._set_history_starting_message_id(session_id, message_id)
                        logger.info(
                            f"[Agent] History marker set: send_messages[{candidate_boundary_idx}] "
                            f"-> history[{-history_backword_idx}] -> {message_id}"
                        )
        except Exception as e:
            logger.error(f"[Agent] Failed to update history marker for session {session_id}: {e}")
            logger.exception(e)

    async def _detect_action_call(
        self,
        content: str,
        last_end_pos: int,
        session_id: str,
        current_round: int,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Detect new action call in the content, skipping code blocks.
        Yields each action call dict as it's found.
        For wait actions, yields the action_call first (so frontend receives it),
        then blocks waiting for client to signal continue.
        """
        code_block_pattern = re.compile(r"(```[\s\S]+?```|`[^`\n]+?`|```[\s\S]+?$|`[^`\n]+?$)")
        action_pattern = re.compile(r'<action-([\w-]+)(?:\s+args=["\']([^<>"\']*)["\'])?\s*/>', re.DOTALL)

        # Disable actions in code blocks before searching
        content = code_block_pattern.sub(lambda match:
            match.group(0).replace('`', '·').replace('<action-', '<xxxxxx-')
        , content)

        match = action_pattern.search(content, last_end_pos)
        if not match:
            return

        action_call = {
            "name": match.group(1),
            "args": match.group(2) or "",
        }
        last_end_pos = match.end()

        # Yield action_call to frontend
        yield { "action_call": action_call }

        # Yield position update for caller
        yield { "end_pos": last_end_pos }

        logger.info(
            f"[Agent] <action-{action_call['name']} args=\"{action_call['args']}\" />, round: {current_round}"
        )

        # For wait action, block after yielding
        if action_call["name"] == "wait":
            await self._wait_for_client_actions(session_id)

    async def _wait_for_client_actions(self, session_id: str):
        """
        Wait for client to signal continue after a wait action.
        Uses asyncio.Event for efficient blocking per session.
        """
        event = self.client_action_waiters.setdefault(session_id, asyncio.Event())

        try:
            await asyncio.wait_for(event.wait(), timeout=30)
            event.clear()
            logger.info(f"[Agent] Client continue received, session_id: {session_id}")
        except asyncio.TimeoutError:
            logger.warning(f"[Agent] Wait for client continue timed out, session_id: {session_id}")

    async def _execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        host_tools: List[Dict[str, Any]],
        session_id: str,
        active_time: float,
        history: List[Dict[str, Any]],
        current_round: int,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Execute all tool calls in order.
        Yields tool message placeholders and content chunks.
        """
        host_tool_names = [t["function"]["name"] for t in host_tools]

        for tool_call in tool_calls:
            if not await self._check_active_time(session_id, active_time):
                return

            is_host_tool = tool_call["function"]["name"] in host_tool_names

            # Start new tool message placeholder
            tool_message = self.session_manager.create_message({
                "role": "tool",
                "content": "",
                "name": tool_call["function"]["name"],
                "tool_call_id": tool_call["id"],
                "streaming": True,
            })

            # Add tool message placeholder to history
            history.append(tool_message)
            yield tool_message

            if is_host_tool:
                # Host tool: execute directly
                tool_result = await self.tool_manager.execute(tool_call)
                tool_message.update(tool_result)
            else:
                # Client tool: wait for result
                tool_result = await self._wait_for_client_tool_result(
                    session_id=session_id,
                    tool_call_id=tool_call["id"]
                )
                tool_message.update(tool_result)

            # Save tool message to session manager
            tool_message["streaming"] = False
            await self.session_manager.save_message(session_id, tool_message)

            # Send tool result
            yield {"content": tool_message.get("summary") or ""}

            logger.info(
                f"[Agent] Call {'host' if is_host_tool else 'client'} tool {tool_call['function']['name']}, "
                f"round: {current_round}, message: {json.dumps(tool_message, ensure_ascii=False)}"
            )

    async def _wait_for_client_tool_result(
        self,
        session_id: str,
        tool_call_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        轮询等待 client tool 结果回传
        """
        waiter = self.client_tool_waiters.setdefault(session_id, {})
        expire_at = time.time() + 60
        
        while time.time() < expire_at:
            if waiter.get(tool_call_id):
                return waiter[tool_call_id]
            await asyncio.sleep(0.1)
        
        logger.warning(f"[Agent] Client tool {tool_call_id} timed out")
        return {
            "content": json.dumps({"error": "Timeout or no response from client tool."}),
            "summary": "Client timeout or no response"
        }
