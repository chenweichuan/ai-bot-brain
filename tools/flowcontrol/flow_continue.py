from typing import Dict, Any
from tools.base import Tool


class FlowContinueTool(Tool):
    """Signal that the task is not yet complete and another thinking round should continue"""
    
    name = "flow_continue"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Signal that the task is not yet complete and another thinking round should continue. Use after outputting a response when further steps are still needed to finish.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute workflow continue operation"""
        result = "Continuing to next thinking round to complete remaining steps."
        summary = "🔄 Continuing..."
        return (result, summary)