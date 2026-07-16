import json
import shlex
from typing import Dict, Any
from tools.base import Tool
from providers.computer.client import ComputerClient


class WriteFileTool(Tool):
    """Write file tool - write content to files directly"""
    """IMPORTANT: Avoid write long content in one call"""
    
    name = "write_file"
    
    def __init__(self):
        super().__init__()
        self.computer_client = ComputerClient.get_instance()
        self.os_workspace = self.computer_client.get_os_workspace()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "File writer - Write content to a file in small segments. "
                    f"Access is restricted to: your home (default), workspace ({self.os_workspace}) and /tmp directories. "
                    "Priority SHOULD be given to the use of workspace. "
                    "Note: Invoke only when there is a clear need to write file"
                    "⚠️ CRITICAL CHUNKING RULES: "
                    "1. Each call MUST have segment < 1000 characters AND < 50 lines; "
                    "2. When write a new file, first call: mode='start', subsequent calls: mode='append', "
                    "split content into logical segments (paragraphs, lists, table rows); "
                    "3. NEVER write everything in one call",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "File path (relative from home or absolute from workspace).",
                        },
                        "mode": {
                            "type": "string",
                            "description": "'start'=file start (overwrite), 'append'=add to end",
                            "enum": ["start", "append"],
                        },
                        "line_count": {
                            "type": "integer",
                            "description": "Number of lines planned in this segment (must be < 50).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Content segment (MUST be < 1000 chars AND < 50 lines).",
                        },
                    },
                    "required": ["file_path", "mode", "line_count", "content"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute file put operation"""
        tool_args = json.loads(arguments)
        file_path = tool_args.get("file_path")
        mode = tool_args.get("mode")
        content = tool_args.get("content", "")
        preview = content[:50].replace('\n', ' ') + '...' if len(content) > 50 else content.replace('\n', ' ')

        # Validate mode
        if mode not in ["start", "append"]:
            error_msg = f"Error: Invalid mode '{mode}'. Must be one of: start, append"
            summary = f"❌ Invalid mode '{mode}'. Must be 'start' or 'append'"
            return (error_msg, summary)

        try:
            # Prepare file parent directory
            prepare_command = f"mkdir -p $(dirname {shlex.quote(file_path)})"
            await self.computer_client.exec_command(prepare_command)

            # Write content using printf to properly preserve newlines
            # printf correctly handles newlines and special characters
            if mode == "append":
                write_command = f"printf '\n%s' {shlex.quote(content)} >> {shlex.quote(file_path)}"
            else:
                write_command = f"printf '%s' {shlex.quote(content)} > {shlex.quote(file_path)}"
            
            # Execute command
            returncode, stdout, stderr = await self.computer_client.exec_command(write_command)
            
            if returncode != 0:
                error_msg = f"Error: Failed to write file '{file_path}': {stdout} {stderr}"
                summary = f"❌ Failed to write to file ({file_path}): {stdout[:100]} {stderr[:100]}".replace('\n', ' ')
                return (error_msg, summary)

            result = f"File written successfully:\n\n" \
                f"  Path: {file_path}\n" \
                f"  Mode: {mode}\n"
            summary = f"✅ Successfully {'appended to' if mode == 'append' else 'overwrote'} file ({file_path}): {preview}"

            return (f"{result}\n\nNote: The above is the result of your file write operation.", summary)
        except Exception as e:
            error_msg = f"Error: Failed to write file '{file_path}': {str(e)}"
            summary = f"❌ Failed to write to file ({file_path}): {str(e)[:100]}".replace('\n', ' ')
            return (error_msg, summary)
