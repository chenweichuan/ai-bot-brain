"""
Transcribe voice tool - speech to text conversion
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.speech import SpeechClient


class TranscribeVoiceTool(Tool):
    """Transcribe voice tool (speech to text)"""
    
    name = "transcribe_voice"
    
    def __init__(self):
        super().__init__()
        self.speech_client = SpeechClient.get_instance()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Transcribe audio/video files from host file paths or remote URLs, converting speech to text, up to 5 items per round.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "array",
                            "description": "List of host audio/video file paths or remote audio/video URLs, up to 5 items.",
                            "items": {
                                "type": "string",
                                "description": "Local audio/video file path or remote audio/video URL"
                            }
                        },
                    },
                    "required": ["input"]
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute voice transcription"""
        tool_args = json.loads(arguments)
        input_content = tool_args.get("input")
        
        # 兼容单个字符串输入的情况
        if isinstance(input_content, str):
            input_list = [input_content]
        else:
            input_list = input_content
            
        content = ""
        all_texts = []
        success_count = 0
        fail_count = 0
        
        for idx, input_item in enumerate(input_list, 1):
            try:
                # 调用provider层的语音转文字方法
                text = await self.speech_client.speech_to_text(input_item)
                all_texts.append(text)
                content += f"✅ [{idx}/{len(input_list)}] Speech to text completed for: {input_item}\n" \
                    f"🔊 Transcribed text:\n{text}\n\n"
                success_count += 1
            except Exception as e:
                error_msg = str(e)
                content += f"❌ [{idx}/{len(input_list)}] Failed to load audio/video content from: {input_item}\nError: {error_msg}\n\n"
                fail_count += 1
        
        total_length = sum(len(t) for t in all_texts)
        if fail_count == 0:
            summary = f"✅ Converted {success_count} audio/video files to text (total length: {total_length} chars)"
        elif success_count == 0:
            summary = f"❌ All {fail_count} audio/video files failed to process"
        else:
            summary = f"⚠️ Processed {success_count} successful, {fail_count} failed (total text length: {total_length} chars)"
        
        return (content.strip(), summary)
