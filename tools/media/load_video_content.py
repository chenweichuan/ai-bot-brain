"""
Video content loading tool
"""
import json
from typing import Dict, Any
from tools.base import Tool


class LoadVideoContentTool(Tool):
    """Video content loading tool"""
    
    name = "load_video_content"
    
    def __init__(self):
        super().__init__()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Load video content from local file path or remote URL, support video-related interaction scenarios.",
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
        """Execute video content loading"""
        tool_args = json.loads(arguments)
        input = tool_args.get("input")
        
        # Format video data
        content = [{
            "type": "video",
            "video": {
                "url": input,
                "detail": "low",
            },
        }]
        
        summary = f"✅ Successfully prepared 1 video."
        
        return (content, summary)