"""
Image content loading tool
"""
import json
from typing import Dict, Any
from tools.base import Tool


class LoadImageContentTool(Tool):
    """Image content loading tool"""
    
    name = "load_image_content"
    
    def __init__(self):
        super().__init__()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Load image content from local file paths or remote URLs, support vision-related interaction scenarios.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "array",
                            "description": "List of local image file paths or remote image URLs, up to 5 items.",
                            "items": {
                                "type": "string",
                                "description": "Local image file path or remote image URL"
                            }
                        },
                    },
                    "required": ["input"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[list[dict], str]:
        """Execute image content loading"""
        tool_args = json.loads(arguments)
        input = tool_args.get("input", [])
        
        # Format image data
        content = []
        for input_item in input:
            content.append({
                "type": "image",
                "image": {
                    "url": input_item,
                    "detail": "high",
                },
            })
        
        summary = f"✅ Successfully prepared {len(input)} image(s)."
        
        return (content, summary)
