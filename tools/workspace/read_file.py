import json
import shlex
from typing import Dict, Any
from tools.base import Tool
from providers.computer.client import ComputerClient


class ReadFileTool(Tool):
    """Read file tool - read content from files"""
    
    name = "read_file"
    
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
                "description": "File reader - Read content from files. "
                    f"Access is restricted to: your home (default), workspace ({self.os_workspace}) and /tmp directories. "
                    "Priority SHOULD be given to the use of workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "File path (relative from home or absolute from workspace).",
                        },
                        "with_line_numbers": {
                            "type": "boolean",
                            "description": "Add line numbers to the output.",
                            "default": False,
                        },
                    },
                    "required": ["file_path"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute file read operation"""
        tool_args = json.loads(arguments)
        file_path = tool_args.get("file_path")
        with_line_numbers = tool_args.get("with_line_numbers", False)

        try:
            read_command = f"cat {'-n ' if with_line_numbers else ''}{shlex.quote(file_path)}"
            returncode, stdout, stderr = await self.computer_client.exec_command(read_command)
            
            if returncode != 0:
                error_msg = f"Error: Failed to read file '{file_path}': {stdout} {stderr}"
                summary = f"❌ Failed to read file ({file_path}): {stdout[:100]} {stderr[:100]}".replace('\n', ' ')
                return (error_msg, summary)

            result = f"File content:\n\n{stdout}"
            summary = f"✅ Successfully read file ({file_path})."

            return (result, summary)
        except Exception as e:
            error_msg = f"Error: Failed to read file '{file_path}': {str(e)}"
            summary = f"❌ Failed to read file ({file_path}): {str(e)[:100]}".replace('\n', ' ')
            return (error_msg, summary)