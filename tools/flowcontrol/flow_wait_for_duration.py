import asyncio
import json
from typing import Dict, Any
from tools.base import Tool


class FlowWaitForDurationTool(Tool):
    """Mark the entire task workflow as wait for specified duration seconds."""
    
    name = "flow_wait_for_duration"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark the entire task workflow to pause and wait for the specified duration in seconds before resuming subsequent steps.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration": {
                            "type": "number",
                            "description": "Duration in seconds to wait.",
                            "default": 3.0
                        },
                    },
                    "required": ["duration"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute workflow complete operation"""
        tool_args = json.loads(arguments)
        duration = float(tool_args.get("duration", 3.0))
        
        await asyncio.sleep(duration)
        result = f"Workflow flow had waited for {duration} seconds."
        summary = f"⏳ Workflow had waited for {duration} seconds"
        
        return (result, summary)