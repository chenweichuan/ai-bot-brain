from typing import Dict, Any
from tools.base import Tool


class FlowCompleteTool(Tool):
    """Mark the response process as fully completed and terminate recursion"""
    
    name = "flow_complete"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark this response process as fully completed.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute response complete operation"""
        result = "Response flow marked as fully completed."
        summary = "✅ Response complete"
        return (result, summary)