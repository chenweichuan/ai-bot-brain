from typing import Dict, Any
from tools.base import Tool


class FlowWaitForInputTool(Tool):
    """Mark the entire task workflow as waiting for next input and terminate recursion."""
    
    name = "flow_wait_for_input"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark the entire task workflow to pause indefinitely and wait for subsequent user input before proceeding with further steps.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute workflow wait for next input operation"""
        result = "Workflow flow marked as infinitely waiting for next user input."
        summary = "⏳ Workflow wait for next input"
        return (result, summary)