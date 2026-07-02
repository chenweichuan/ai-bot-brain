"""
QR code recognition tool
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.qrcode.client import QrcodeClient


class RecognizeQRCodeTool(Tool):
    """QR code recognition tool"""
    
    name = "recognize_qrcode"
    
    def __init__(self):
        super().__init__()
        self.qrcode_client = QrcodeClient.get_instance()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Recognize and decode QR code from an image.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "Local image file path or remote image URL containing the QR code",
                        },
                    },
                    "required": ["input"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute QR code recognition"""
        tool_args = json.loads(arguments)
        input = tool_args.get("input")
        
        result = await self.qrcode_client.recognize_qrcode(input)
        
        # Format result
        if result:
            content = "Recognized QR code content:\n\n"
            content += f"{result}\n\n"
            content += "Note: The above is the decoded content from the QR code."
            summary = f"✅ Successfully recognized QR code: {input}"
        else:
            content = "QR code recognition failed"
            summary = f"❌ {content}"
        
        return (content, summary)
