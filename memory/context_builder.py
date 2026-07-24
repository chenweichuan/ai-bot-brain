"""
Context Manager for handling conversation context and history
"""
import copy
from datetime import datetime
from typing import Dict, List, Optional, Any

from common.log import logger
from common.message import count_text_units, stringify_message_content, count_messages_text_units
from config import conf


class ContextBuilder:
    """Manager for building and managing conversation context"""
    _instance = None

    MAX_TEXT_UNITS = 100000
    MAX_MESSAGES = 100
    MAX_MODEL_ROUNDS = 25

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        pass
    
    def build_context(
        self,
        history: List[Dict[str, Any]] = None,
        memory: str = "",
        instructions: str = "",
        tools: List[Dict[str, Any]] = None,
        max_text_units: int = MAX_TEXT_UNITS,
        max_messages: int = MAX_MESSAGES,
    ) -> List[Dict[str, Any]]:
        """Prepare messages for LLM request"""
        history = copy.deepcopy(history or [])
        tools = tools or []
        max_text_units = min(abs(max_text_units), self.MAX_TEXT_UNITS)
        max_messages = min(abs(max_messages), self.MAX_MESSAGES)
        
        # Build system message
        system_message = self.build_system_message(
            memory=memory,
            instructions=instructions,
            tools=tools,
        )
        
        # Calculate text units for system message
        system_message_text_units = count_messages_text_units([system_message])

        # Filter history
        history = self._filter_history(
            history,
            max_text_units - system_message_text_units,
            max_messages - 1
        )

        # Combine system message and history
        messages = [system_message] + history
        # Create multimodal parts placeholder
        multimodal_parts = []

        # Find the last user message
        last_user_idx = len(messages) - 1 - next((i for i, msg in enumerate(reversed(messages)) if msg["role"] == "user"), 0)
        # Find the last assistant message
        last_assistant_idx = len(messages) - 1 - next((i for i, msg in enumerate(reversed(messages)) if msg["role"] == "assistant"), 0)

        # 如果最新消息是AI回复的，则提取出该新消息触发的工具里给出的多媒体资源
        if last_assistant_idx > last_user_idx:
            for msg in messages[last_assistant_idx:]:
                if msg["role"] == "tool" and isinstance(msg["content"], list):
                    for part in msg["content"]:
                        if part.get("type") in ["image", "video"]:
                            multimodal_parts.append(part)

        # 消息内容格式调整
        valid_msg_fields = ["role", "reasoning_content", "content", "tool_call_id", "tool_calls"]
        for index, msg in enumerate(messages):
            # 将消息内容结构统一转为纯文本，降低非文本资源导致的性能消耗
            msg["content"] = stringify_message_content(msg["content"])
            # 消息发送人处理
            if msg["role"] == "user" and msg.get("name"):
                msg["content"] = f"User (named {msg['name']}) " \
                    f"at {datetime.fromtimestamp(msg['timestamp'] // 1_000).strftime('%Y-%m-%d %H:%M:%S')} " \
                    f"says:\n\n{msg['content']}"
            # 只保留有效字段
            messages[index] = {k: v for k, v in msg.items() if k in valid_msg_fields}

        # 追加最新AI消息工具调用里返回的多媒体资源
        if multimodal_parts:
            multimodal_msg = {
                "role": "user",
                "content": [],
            }
            for part in multimodal_parts:
                multimodal_msg["content"].append(part)
            messages.append(multimodal_msg)

        logger.info(f"[ContextManager] Send system message text units: {count_messages_text_units([system_message])}")
        logger.info(f"[ContextManager] Send history messages text units: {count_messages_text_units(history)}, length: {len(history)}")
        logger.info(f"[ContextManager] Send tools text units: {count_text_units(str(tools))}, length: {len(tools)}")

        return messages

    def build_system_message(
        self,
        memory: str = "",
        instructions: str = "",
        tools: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tools = tools or []

        owner_name = conf().get("owner_name", "")
        bot_name = conf().get("bot_name", "Bot")
        bot_alias = conf().get("bot_alias", "")
        prompts = []

        role_prompt = "IMPORTANT: \n" \
            + f"- You are an intelligent assistant named {bot_name}" + (f", a.k.a. {bot_alias}" if bot_alias else "") + ".\n" \
            + f"{owner_name} is your owner, and you only trust your owner and those who have been confirmed trustworthy by your owner.\n" \
            + "- Your inner thinking mode is running asynchronously in the background all the time to handle anything that needs follow-up or completion.\n" \
            + "- By default, provide direct responses. Only engage in deep thinking when encountering complex, in-depth questions that require thorough analysis.\n" \
            + "- When you chat and interact with different users, you MUST 100% protect and respect the personal information of other users stored in your memory.\n" \
            + f"- Now time is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        # Role Prompt
        prompts.append(role_prompt)

        if memory:
            prompts.append(memory)
 
        prompts.append(
            "Special Markdown syntax:\n"
            "------\n"
            "- Audio file: `!audio[Title](audio_url)`\n"
            "- Video file: `!video[Title](video_url)`\n"
            "- Webpage file: `!webpage[Title](webpage_url)`\n"
            "------"
        )

        if tools:
            prompts.append(
                "Your available tools:\n"
                "------\n"
                + "\n".join(
                    f"- {tool['function']['name']}: {tool['function']['description']}"
                    for tool in tools
                )
                + "\n------\n"
                + "Note: Prioritize running multiple independent tool calls in parallel within a single response to reduce interaction rounds, "
                + "while avoiding unnecessary invocations when a direct answer is possible."
            )

        # Role Prompt again
        prompts.append(role_prompt)

        if instructions:
            prompts.append(instructions)
 
        return {
            "role": "system",
            "content": "\n\n".join(prompts),
        }

    def _filter_history(self, history: List[Dict[str, Any]], max_text_units: int, max_messages: int) -> List[Dict[str, Any]]:
        """Filter history messages"""
        # Filter valid messages
        history = [
            msg for msg in history
            if msg and (msg.get("content") or msg.get("tool_calls"))
        ]

        # Get recent history messages as many as possible based on text units limit
        recent_history = []
        cumu_text_units = 0
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            msg_text_units = count_messages_text_units([msg])
            if cumu_text_units + msg_text_units > max_text_units:
                break
            cumu_text_units += msg_text_units
            recent_history.insert(0, msg)

        # Limit the number of history messages
        recent_history = recent_history[-max_messages:]

        # Limit the number of model rounds
        all_assistant_idxes = [i for i, msg in enumerate(recent_history) if msg["role"] == "assistant"]
        if len(all_assistant_idxes) > self.MAX_MODEL_ROUNDS:
            recent_history = recent_history[all_assistant_idxes[-self.MAX_MODEL_ROUNDS]:]

        # Get tool call IDs from call messages
        tool_call_ids_in_call_msgs = []
        for msg in recent_history:
            for tc in msg.get("tool_calls") or []:
                if tc and tc.get("id"):
                    tool_call_ids_in_call_msgs.append(tc["id"])
        
        # Get tool call IDs from tool messages
        tool_call_ids_in_tool_msgs = [
            msg.get("tool_call_id") for msg in recent_history
            if msg.get("role") == "tool"
        ]

        # Deal with invalid call & tool messages
        valid_recent_history = []
        for msg in recent_history:
            if msg.get("tool_calls"):
                # Remove tool calls without corresponding tool messages
                msg["tool_calls"] = [
                    tc for tc in msg["tool_calls"]
                    if tc and tc.get("id") in tool_call_ids_in_tool_msgs
                ]
                # Keep message only if it has valid tool calls
                if msg["tool_calls"]:
                    valid_recent_history.append(msg)
            elif msg.get("role") == "tool":
                # Remove tool messages without corresponding call messages
                if msg.get("tool_call_id") in tool_call_ids_in_call_msgs:
                    valid_recent_history.append(msg)
            else:
                valid_recent_history.append(msg)

        return valid_recent_history
