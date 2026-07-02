"""
QR code generation tool
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.qrcode.client import QrcodeClient
from providers.short_link import ShortLinkClient


class GenerateQRCodeTool(Tool):
    """QR code generation tool"""
    
    name = "generate_qrcode"
    
    def __init__(self):
        super().__init__()
        self.qrcode_client = QrcodeClient.get_instance()
        self.short_link_client = ShortLinkClient()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Generate a QR code image from text data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "string",
                            "description": "The text data to be encoded in the QR code.",
                        },
                    },
                    "required": ["data"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute QR code generation"""
        tool_args = json.loads(arguments)
        data = tool_args.get("data")
        
        result = await self.qrcode_client.generate_qrcode(data)
        
        content = ""
        
        # Format result
        if result:
            # Image URL returned
            short_link = await self.short_link_client.convert_link_to_short(result)
            content += f"![Generated QR Code]({short_link})\n\n"
            content += "Note: The above is the Markdown for the generated QR code image."
            summary = f"✅ Successfully generated QR code: {short_link}"
        else:
            content = "QR code generation failed"
            summary = f"❌ {content}"
        
        return (content, summary)
