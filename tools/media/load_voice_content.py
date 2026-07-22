"""
Load voice content tool - speech to text conversion
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.speech import SpeechClient


class LoadVoiceContentTool(Tool):
    """Load voice content tool (speech to text)"""
    
    name = "load_voice_content"
    
    def __init__(self):
        super().__init__()
        self.speech_client = SpeechClient.get_instance()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Load and transcribe voice content from local file path or remote URL, convert speech to text, up to 5 items per round.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "array",
                            "description": "List of local voice file paths or remote voice URLs, up to 5 items.",
                            "items": {
                                "type": "string",
                                "description": "Local voice file path or remote voice URL"
                            }
                        },
                    },
                    "required": ["input"]
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute speech to text operation"""
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
                content += f"❌ [{idx}/{len(input_list)}] Failed to load voice content from: {input_item}\nError: {error_msg}\n\n"
                fail_count += 1
        
        total_length = sum(len(t) for t in all_texts)
        if fail_count == 0:
            summary = f"✅ Converted {success_count} voice files to text (total length: {total_length} chars)"
        elif success_count == 0:
            summary = f"❌ All {fail_count} voice files failed to process"
        else:
            summary = f"⚠️ Processed {success_count} successful, {fail_count} failed (total text length: {total_length} chars)"
        
        return (content.strip(), summary)
