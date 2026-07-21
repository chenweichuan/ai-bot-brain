from typing import Dict, Any
from tools.base import Tool


class FlowCompleteTool(Tool):
    """Mark the entire task workflow as fully completed and terminate recursion"""
    
    name = "flow_complete"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark the entire task workflow as fully completed, with no remaining steps or follow-up actions required.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute workflow complete operation"""
        result = "Workflow flow marked as fully completed."
        summary = "✅ Workflow complete"
        return (result, summary)