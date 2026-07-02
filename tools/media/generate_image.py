"""
Image generation tool
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.t2i.client import T2IClient
from providers.short_link import ShortLinkClient
from config import conf


class GenerateImageTool(Tool):
    """Image generation tool"""
    
    name = "generate_image"
    
    def __init__(self):
        super().__init__()
        self.image_model = conf().get("image_model")
        self.short_link_client = ShortLinkClient()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Generate an image with AI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "A description of what to draw using AI.",
                        },
                        "references": {
                            "type": "array",
                            "description": "Images to be modified or referenced; first is base image; up to 9.",
                            "items": {"type": "string", "description": "Image URL"},
                        },
                    },
                    "required": ["prompt"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute image generation"""
        tool_args = json.loads(arguments)
        prompt = tool_args.get("prompt")
        ref_image_urls = tool_args.get("references")
        
        try:
            result = await T2IClient.factory(self.image_model).generate(prompt, self.image_model, ref_image_urls)

            # Format result
            if result:
                # Image URL returned
                short_link = await self.short_link_client.convert_link_to_short(result)
                content = [
                    {
                        "type": "image",
                        "image": {
                            "url": short_link,
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": "Note: The above is the generated image result. You MUST show it to the user by Markdown format.",
                    }
                ]
                if ref_image_urls:
                    summary = f"✅ Successfully generated image from {len(ref_image_urls)} reference(s)"
                else:
                    summary = "✅ Successfully generated image"
            else:
                content = "Image generation failed"
                if ref_image_urls:
                    summary = f"❌ {content} from reference(s)"
                else:
                    summary = f"❌ {content}"
        except Exception as e:
            error_msg = str(e)
            content = f"❌ Failed to generate image: {error_msg}"
            summary = f"❌ Image generation failed: {error_msg[:100]}".replace("\n", " ")
        
        return (content, summary)
