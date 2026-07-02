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
                "description": "Load video content from local file paths or remote URLs, support video-related interaction scenarios.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "array",
                            "description": "List of local video file paths or remote video URLs, up to 3 items.",
                            "items": {
                                "type": "string",
                                "description": "Local video file path or remote video URL"
                            }
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
        content = []
        for input_item in input:
            content.append({
                "type": "video",
                "video": {
                    "url": input_item,
                    "detail": "low",
                },
            })
        
        summary = f"✅ Successfully prepared {len(input)} video(s)."
        
        return (content, summary)