from typing import Dict, Any
from tools.base import Tool


class FlowContinueTool(Tool):
    """Mark the entire task workflow as not complete and request continuation"""
    
    name = "flow_continue"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark the entire task workflow as not yet completed, with remaining steps or follow-up actions still pending.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute workflow continue operation"""
        result = "Workflow flow marked as not completed yet."
        summary = "🔄 Workflow continuing"
        return (result, summary)