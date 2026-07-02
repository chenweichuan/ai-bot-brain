"""
Message recall tool that retrieves messages by clues
"""
from datetime import datetime
import json
import time
from typing import List, Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager
from common.log import logger


class RecallMessagesTool(Tool):
    """Tool for recalling historical messages by clues"""
    
    name = "recall_messages"
    
    MAX_QUERY_CLUES = 10
    
    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Retrieve historical conversation messages from memory through relevant impression clues. "
                    "Use this tool when you need to recall specific past conversation messages based on related impression clues.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clues": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"List of relevant impression clues to query (max {self.MAX_QUERY_CLUES}).",
                        },
                        "time_before": {
                            "type": "string",
                            "description": f"`YYYY-MM-DD HH:MM:SS` 24-hour format (e.g., '{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}') to filter messages before this time point",
                        }
                    },
                    "required": ["clues"]
                }
            }
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """
        Execute tool call and return result content
        
        Args:
            arguments: Tool arguments as JSON string
            
        Returns:
            Tuple of (full_result, summary) where:
                full_result: Complete tool result content as string
                summary: Simplified summary message for frontend display
        """
        try:
            args = json.loads(arguments) if arguments else {}
            
            # Parse input parameters
            clues: List[str] = [clue.strip() for clue in args.get("clues") or []][:self.MAX_QUERY_CLUES]
            time_before: str = args.get("time_before")
            
            # Convert time string to timestamp (milliseconds), plus one millisecond to exclude the exact time point
            if time_before:
                timestamp_before = time.mktime(time.strptime(time_before, '%Y-%m-%d %H:%M:%S')) * 1_000 + 1_000
            else:
                timestamp_before = time.time_ns() / 1_000_000
            
            max_message_text_units = self.impression_manager.MESSAGE_TEXT_UNITS_PER_SET
            
            # Query messages by clues
            message_tuples = await self.impression_manager.get_messages_by_clues(
                clues,
                max_text_units=max_message_text_units,
                timestamp=timestamp_before
            )
            # Sort messages by ascending chronological order
            message_tuples = list(reversed(message_tuples))

            # Assemble final result
            result_parts = []
            
            if message_tuples:
                result_parts.append(f"### Potentially Chronological Related Messages:\n")
                result_parts.append("------")
                result_parts.extend([
                    f"{message}\n------"
                    for (message, _) in message_tuples
                ])
                result_parts.append("\n")
            else:
                result_parts.append(
                    "No raw messages found linked to this clue. "
                    "The original messages may have been automatically cleaned up, "
                    "or specific clues have no associated message IDs.\n"
                )
            
            full_result = "\n".join(result_parts)
            
            # Create summary
            summary = f"✅ Recalled {len(message_tuples)} potential messages from {len(clues)} clues"
            summary += f" before {time_before}" if time_before else ""
            
            logger.info(
                f"Recalled messages for clues({', '.join(clues)}) before({time_before}), "
                f"count: {len(message_tuples)} messages"
            )

            return (full_result, summary)
        except Exception as e:
            logger.error(f"[RecallMessagesTool] Error executing tool: {e}")
            logger.exception(e)
            error_msg = f"Error: Failed to recall messages - {str(e)}"
            summary = f"❌ Failed to recall messages: {str(e)[:100]}".replace("\n", " ")
            return (error_msg, summary)