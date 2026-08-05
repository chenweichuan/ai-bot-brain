"""
Analyze image tool - load images for vision analysis
"""
import json
from typing import Dict, Any
from tools.base import Tool


class AnalyzeImageTool(Tool):
    """Analyze image tool - load images for vision analysis"""
    
    name = "analyze_image"
    
    def __init__(self):
        super().__init__()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Analyze images from host file paths or remote URLs using vision capabilities, up to 10 items per round.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "array",
                            "description": "List of host image file paths or remote image URLs, up to 10 items.",
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
        """Execute image analysis preparation"""
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
        
        summary = f"✅ Loaded {len(input)} image(s) for analysis."
        
        return (content, summary)
