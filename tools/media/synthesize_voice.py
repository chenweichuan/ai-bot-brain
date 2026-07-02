"""
Synthesize voice tool - text to speech conversion
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.speech import SpeechClient
from providers.short_link import ShortLinkClient


class SynthesizeVoiceTool(Tool):
    """Synthesize voice tool (text to speech)"""
    
    name = "synthesize_voice"
    
    def __init__(self):
        super().__init__()
        self.speech_client = SpeechClient.get_instance()
        self.short_link_client = ShortLinkClient()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Synthesize voice from text content, generate voice files and return links for playback.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "Text content to synthesize into voice, strictly no Arabic numerals allowed, must use digit text corresponding to the target language. (MUST be < 50 words)"
                        },
                    },
                    "required": ["input"]
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute text to speech operation"""
        tool_args = json.loads(arguments)
        input_content = tool_args.get("input")
        
        try:
            # 调用provider层的文字转语音方法
            voice_url = await self.speech_client.text_to_speech(input_content)
            
            # 转换为短链接
            short_link = await self.short_link_client.convert_link_to_short(voice_url)
            content = f"!audio[Synthesized Voice]({short_link})\n\n"
            content += "Note: The above is the Markdown for the synthesized voice result. You MUST show it to the user as-is."
            summary = "✅ Successfully synthesized voice"
        except Exception as e:
            error_msg = str(e)
            content = f"❌ Failed to synthesize voice: {error_msg}"
            summary = f"❌ Synthesize voice failed: {error_msg[:100]}".replace("\n", " ")
        
        return (content, summary)
