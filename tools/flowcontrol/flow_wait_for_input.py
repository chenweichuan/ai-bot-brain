from typing import Dict, Any
from tools.base import Tool


class FlowWaitForInputTool(Tool):
    """Mark the response process as waiting for next input and terminate recursion."""
    
    name = "flow_wait_for_input"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark this response process as infinitely waiting for next input.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute response wait for next input operation"""
        result = "Response flow marked as infinitely waiting for next input."
        summary = "⏳ Response wait for next input"
        return (result, summary)