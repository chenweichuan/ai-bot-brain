"""
Analyze video tool - load video for vision analysis
"""
import json
from typing import Dict, Any
from tools.base import Tool


class AnalyzeVideoTool(Tool):
    """Analyze video tool - load video for vision analysis"""
    
    name = "analyze_video"
    
    def __init__(self):
        super().__init__()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Analyze a video from a host file path or remote URL using vision capabilities. One video per round only.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "Local video file path or remote video URL."
                        },
                    },
                    "required": ["input"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[list[dict], str]:
        """Execute video analysis preparation"""
        tool_args = json.loads(arguments)
        input = tool_args.get("input")
        
        # Format video data
        content = [{
            "type": "video",
            "video": {
                "url": input,
                "fps": 5,
            },
        }]
        
        summary = f"✅ Loaded 1 video for analysis."
        
        return (content, summary)