"""
Agent for handling LLM requests with tools calling and looping capability
"""
import asyncio
import json
import re
import time
import uuid
from typing import Dict, List, Any, AsyncGenerator, Optional

from common.log import logger
from common.message import count_text_units
from common.redis import RedisClient
from config import conf
from memory.context_builder import ContextBuilder
from memory.impression_manager import ImpressionManager
from memory.session_manager import SessionManager
from providers.llm.client import LlmClient
from actions.manager import ActionManager
from actions.flowcontrol import WaitAction
from tools.manager import ToolManager
from tools.flowcontrol import FlowContinueTool, FlowWaitForInputTool, FlowCompleteTool


class AgentService:
    """Agent that handles LLM requests with tools calling and looping"""
    _instance = None

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
        self.action_manager = ActionManager.get_instance()
        self.tool_manager = ToolManager.get_instance()
        self.redis_client = RedisClient.get_instance()

        self.memory_queue = asyncio.Queue(maxsize=100)
        self.is_processing_memory_queue = False
        
        self.bot_name = conf().get("bot_name", "Bot")
        self.KEY_PREFIX = f"{self.bot_name.lower().replace(' ', '_')}:agent"
        
        # Client tool waiters: {session_id: {tool_call_id: result}}
        self.client_tool_waiters: Dict[str, Dict[str, Dict]] = {}
        
        # Pending actions tracking: {session_id: set(action_call_id)}
        self.pending_actions: Dict[str, set] = {}
    
    async def think(
        self,
        username: str = None,
        session_id: str = None,
        messages: List[Dict[str, Any]] = [],
        model: str = conf().get("think_model"),
        instructions: str = "",
        actions: List[Dict[str, str]] = [],
        tools: List[Dict[str, Any]] = [],
        thinking: bool = True,
        temperature: float = 0.2,
        top_p: float = 0.85,
        max_text_units: int = ContextBuilder.MAX_TEXT_UNITS,
        max_messages: int = ContextBuilder.MAX_MESSAGES,
        depth: int = 0,
        max_depth: int = 500,
        active_time: Optional[float] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Main thinking loop that handles tools calling and recursion
        """
        # Prepare session
        session_id = await self._prepare_session(username, session_id)
        yield { "session_id": session_id }

        if active_time and not await self._check_active_time(session_id, active_time):
            logger.info(f"[Agent] Think session {session_id} has been replaced by new request, exiting")
            yield { "finish_reason": "stop" }
            return
        active_time = await self._set_active_time(session_id)

        # Save new messages
        new_msg_ids = await self._save_new_messages(username, session_id, messages)
        yield { "message_ids": new_msg_ids }

        # Prepare memory
        impression_categories = await self.impression_manager.get_recent_categories()
        impression_labels = await self.impression_manager.get_mixed_labels()
        impressions = await self.impression_manager.get_mixed_impressions()
        history = await self.session_manager.get_message_history(
            session_id=session_id, limit=ContextBuilder.MAX_MESSAGES
        )

        # Prepare actions for LLM
        server_actions = await self.action_manager.get_definitions()
        client_actions = actions
        send_actions = server_actions + client_actions

        # Prepare tools for LLM
        server_tools = await self.tool_manager.get_definitions()
        client_tools = tools
        send_tools = server_tools + client_tools

        # Prepare context for LLM
        send_messages = self.context_builder.build_context(
            history=history,
            impression_categories=impression_categories,
            impression_labels=impression_labels,
            impressions=impressions,
            instructions=instructions,
            actions=send_actions,
            tools=send_tools,
            max_text_units=max_text_units,
            max_messages=max_messages,
        )

        logger.info(f"[Agent] Start thinking, depth: {depth}, session_id: {session_id}, active_time: {active_time}")

        # Create bot message placeholder
        bot_message = self.session_manager.create_message({
            "role": "assistant",
            "reasoning_content": "",
            "content": "",
            "tool_calls": None,
            "to_name": username,
        })
        bot_model = None

        # Add bot message placeholder to history
        history.append(bot_message)
        yield bot_message

        # Reset client tool waiters for this session before LLM call
        self.client_tool_waiters[session_id] = {}
        
        # Reset pending actions for this session
        self.pending_actions[session_id] = set()

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
                "top_p": top_p,
            }
            response = await LlmClient.factory(request["model"]).chat(**request)

            # Action processing
            server_action_names = [action["name"] for action in server_actions]
            action_pattern = re.compile(r'<action-([\w-]+)(?:\s+args="(.*?)")?\s*/>', re.DOTALL)
            last_action_end_pos = 0
            
            # Streaming response
            async for chunk in response:
                if not await self._check_active_time(session_id, active_time):
                    logger.info(f"[Agent] Think session {session_id} has been replaced by new request, exiting")
                    yield { "finish_reason": "stop" }
                    return
                
                if not chunk.get("choices"):
                    continue

                delta = chunk["choices"][0].get("delta", {})

                # Send actual model name at the first chunk
                if not bot_model:
                    bot_model = chunk.get("model") or model
                    yield { "model": bot_model }
                
                # Handle reasoning_content
                if delta.get("reasoning_content"):
                    bot_message["reasoning_content"] += delta["reasoning_content"]
                    yield { "reasoning_content": delta["reasoning_content"] }
                
                # Handle content
                if delta.get("content"):
                    bot_message["content"] += delta["content"]
                    yield { "content": delta["content"] }
                    
                    # Detect and yield new action call
                    # Temporarily disable actions in code blocks before searching
                    temp_content = re.compile(r"(```[\s\S]+?```|`[^`\n]+?`|```[\s\S]+?$|`[^`\n]+?$)").sub(lambda match:
                        match.group(0).replace('`', '·').replace('<action-', '<xxxxxx-')
                    , bot_message["content"])
                    # Find new action call after last detection
                    match = action_pattern.search(temp_content, last_action_end_pos)
                    if match:
                        action_call = {
                            "id": uuid.uuid4().hex[:8],
                            "name": match.group(1),
                            "args": match.group(2) or ""
                        }
                        last_action_end_pos = match.end()
                        is_server_action = action_call["name"] in server_action_names
                        
                        # Add to pending actions
                        self.pending_actions[session_id].add(action_call["id"])
                        
                        if is_server_action:
                            if action_call["name"] == WaitAction.name:
                                self.pending_actions[session_id].remove(action_call["id"])
                                await self._wait_for_previous_actions(session_id)
                            else:
                                asyncio.create_task(self._execute_server_action(
                                    session_id, action_call
                                ))
                        else:
                            yield { "action_call": action_call }
                        
                        logger.info(
                            f"[Agent] Call {'server' if is_server_action else 'client'} action "
                            f"{action_call['name']} (id: {action_call['id']}), "
                            f"args: {action_call['args']}, depth: {depth}"
                        )
                
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
 
                # Save message to session manager
                await self.session_manager.save_message(session_id, bot_message)
 
            logger.info(
                f"[Agent] Finish thinking step, depth: {depth}, "
                f"message: {json.dumps(bot_message, ensure_ascii=False)}"
            )

            # Deal with tool calls
            if bot_message.get("tool_calls"):
                server_tool_names = [t["function"]["name"] for t in server_tools]

                # Execute tool calls in original order
                for tool_call in bot_message["tool_calls"]:
                    if not await self._check_active_time(session_id, active_time):
                        logger.info(f"[Agent] Think session {session_id} has been replaced by new request, exiting")
                        yield { "finish_reason": "stop" }
                        return
                    
                    is_server_tool = tool_call["function"]["name"] in server_tool_names
                    
                    # Start new tool message placeholder
                    tool_message = self.session_manager.create_message({
                        "role": "tool",
                        "content": "",
                        "name": tool_call["function"]["name"],
                        "tool_call_id": tool_call["id"]
                    })

                    # Add tool message placeholder to history
                    history.append(tool_message)
                    yield tool_message
                    
                    if is_server_tool:
                        # Server tool: execute directly
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
                    await self.session_manager.save_message(session_id, tool_message)

                    logger.info(
                        f"[Agent] Call {'server' if is_server_tool else 'client'} tool {tool_call['function']['name']}, "
                        f"depth: {depth}, message: {json.dumps(tool_message, ensure_ascii=False)}"
                    )
                    
                    # Send tool result
                    yield { "content": tool_message.get("summary") or "" }
        except Exception as e:
            logger.error(f"[Agent] Think error: {e}")
            logger.exception(e)
            yield { "content": f"```\n{str(e)}\n```" }
            yield { "finish_reason": "error" }
            return
        finally:
            # If bot_message has content or tool_calls, add memory task to queue
            if bot_message.get("content") or bot_message.get("tool_calls"):
                await self._put_memory_queue(
                    username=username,
                    instructions=instructions,
                    history=history.copy(),
                )

        # Check for flow control tools
        should_continue = True if bot_message["content"] or bot_message["tool_calls"] else False
        tool_call_names = [t["function"]["name"] for t in bot_message["tool_calls"] or []]
        if FlowContinueTool.name in tool_call_names:
            should_continue = True
        elif FlowWaitForInputTool.name in tool_call_names:
            should_continue = False
        elif FlowCompleteTool.name in tool_call_names:
            should_continue = False

        # Recursive call
        if should_continue and depth < max_depth:
            # Add small delay to avoid overwhelming the system
            await asyncio.sleep(0.01)
            # 下一步思考
            async for chunk in self.think(
                username=username,
                session_id=session_id,
                messages=[],
                model=model,
                instructions=instructions,
                actions=actions,
                tools=tools,
                thinking=thinking,
                temperature=temperature,
                top_p=top_p,
                max_text_units=ContextBuilder.MAX_TEXT_UNITS,
                max_messages=ContextBuilder.MAX_MESSAGES,
                depth=depth + 1,
                max_depth=max_depth,
                active_time=active_time,
            ):
                yield chunk
        else:
            logger.info(f"[Agent] Think {session_id} is complete")
            yield { "finish_reason": "complete" }

    async def message(
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

    async def client_tool_result(
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
        
        if session_id not in self.client_tool_waiters:
            logger.warning(f"[Agent] No waiter found for session {session_id}")
            return
        
        # Directly store the result
        self.client_tool_waiters[session_id][tool_call_id] = {
            "content": content,
            "summary": summary,
        } if content else None
        
        logger.info(f"[Agent] Received client tool result for {tool_call_id}")

    async def client_action_complete(
        self,
        username: str,
        session_id: str,
        action_call_id: str,
    ):
        """
        客户端上报 action 执行结束
        """
        # Validate session permission
        await self.session_manager.check_user_session(username, session_id)
        
        if action_call_id in self.pending_actions.get(session_id, set()):
            self.pending_actions[session_id].remove(action_call_id)
            logger.info(f"[Agent] Received client action complete for {action_call_id}")
        else:
            logger.warning(f"[Agent] No pending action found for session {session_id} and action {action_call_id}")

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
            "id", "timestamp", 
            "role", "reasoning_content", "content",
            "name", "tool_calls", "tool_call_id",
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

    async def _put_memory_queue(self, **task_params):
        """Add memory task to queue"""
        try:
            if self.memory_queue.full():
                # Remove the oldest item to make space
                self.memory_queue.get_nowait()
                logger.warning(f"[Agent] Memory queue is full. Evicting oldest task to make space.")
            # Try to add to queue
            self.memory_queue.put_nowait(task_params)
            logger.info(f"[Agent] Added memory task to queue. Queue size: {self.memory_queue.qsize()}")
            # Start processing queue if not already processing
            if not self.is_processing_memory_queue:
                asyncio.create_task(self._process_memory_queue())
        except Exception as e:
            logger.error(f"[Agent] Processing impression queue error: {e}")

    async def _process_memory_queue(self):
        """Process memory tasks from queue sequentially"""
        if self.is_processing_memory_queue:
            return
        
        self.is_processing_memory_queue = True
        logger.info(f"[Agent] Started processing memory queue")
        
        try:
            while not self.memory_queue.empty():
                # Get task from queue
                task_params = await self.memory_queue.get()
                
                try:
                    # Slice new turn messages
                    new_turn_messages = self.impression_manager.slice_new_turn_messages(task_params["history"])
                    
                    # Maintain impressions
                    await self.impression_manager.maintain_impressions_by_llm(
                        username=task_params["username"],
                        instructions=task_params["instructions"],
                        messages=new_turn_messages,
                    )
                    
                    logger.info(f"[Agent] Completed memory task, remaining in queue: {self.memory_queue.qsize()}")
                except Exception as e:
                    logger.error(f"[Agent] Failed to process memory task: {e}")
                    logger.exception(e)
                finally:
                    # Mark task as done
                    self.memory_queue.task_done()
                    # Add small delay to avoid overwhelming the system
                    await asyncio.sleep(0.01)
        finally:
            self.is_processing_memory_queue = False
            logger.info(f"[Agent] Finished processing memory queue")

    async def _wait_for_client_tool_result(
        self,
        session_id: str,
        tool_call_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        轮询等待 client tool 结果回传
        """
        expire_at = time.time() + 30
        
        while time.time() < expire_at:
            if self.client_tool_waiters.get(session_id, {}).get(tool_call_id):
                return self.client_tool_waiters[session_id][tool_call_id]
            await asyncio.sleep(1)
        
        logger.warning(f"[Agent] Client tool {tool_call_id} timed out")
        return {
            "content": json.dumps({"error": "Timeout or no response from client tool."}),
            "summary": "Client timeout or no response"
        }

    async def _wait_for_previous_actions(self, session_id: str):
        """Wait for previous actions to complete"""
        if not self.pending_actions.get(session_id):
            return
        
        logger.debug(f"[Agent] Waiting for previous actions to complete, session_id: {session_id}")
        
        expire_at = time.time() + 15
        while time.time() < expire_at:
            if not self.pending_actions.get(session_id):
                return
            await asyncio.sleep(1)

        logger.warning(f"[Agent] Waiting for previous actions to complete timed out, session_id: {session_id}")
        
        # Clear pending actions for this session, to avoid blocking future actions.
        self.pending_actions[session_id].clear()

    async def _execute_server_action(self, session_id: str, action_call: dict):
        """Execute a server action and remove it from pending when done"""
        try:
            await self.action_manager.execute(action_call)
        finally:
            if action_call["id"] in self.pending_actions.get(session_id, set()):
                self.pending_actions[session_id].remove(action_call["id"])
                logger.debug(f"[Agent] Removed server action {action_call['id']} from pending, session: {session_id}")
    