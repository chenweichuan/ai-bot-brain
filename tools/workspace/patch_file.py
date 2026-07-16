import json
import shlex
from typing import Dict, Any
from tools.base import Tool
from providers.computer.client import ComputerClient


class PatchFileTool(Tool):
    """Patch file tool - replace specific lines in files using precise line positioning"""
    
    name = "patch_file"
    
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
                "description": "File patcher - Replace specific lines in files using precise line positioning. "
                    f"Access is restricted to: your home (default), workspace ({self.os_workspace}) and /tmp directories. "
                    "Priority SHOULD be given to the use of workspace. "
                    "Use mode='start' to get file content with line numbers first. "
                    "Note: Invoke only when there is a clear need to modify specific lines in a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "File path (relative from home or absolute from workspace).",
                        },
                        "mode": {
                            "type": "string",
                            "description": "'start'=read file with line numbers, 'patch'=execute patch then read updated file with line numbers",
                            "enum": ["start", "patch"],
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Starting line number to replace (line numbering starts at 1). Required when mode='patch'",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Ending line number to replace (line numbering starts at 1). Required when mode='patch'",
                        },
                        "content": {
                            "type": "string",
                            "description": "New content that will replace the specified lines (use \\n for new lines). Required when mode='patch'",
                        },
                    },
                    "required": ["file_path", "mode"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute file patch operation"""
        tool_args = json.loads(arguments)
        file_path = tool_args.get("file_path")
        mode = tool_args.get("mode")
        start_line = tool_args.get("start_line")
        end_line = tool_args.get("end_line")
        content = tool_args.get("content", "")

        # Validate mode
        if mode not in ["start", "patch"]:
            error_msg = f"Error: Invalid mode '{mode}'. Must be one of: start, patch"
            summary = f"❌ Invalid mode '{mode}'. Must be 'start' or 'patch'"
            return (error_msg, summary)

        try:
            if mode == "patch":
                # Validate required fields for patch mode
                if not start_line or not end_line or content is None:
                    error_msg = "Error: 'start_line', 'end_line', and 'content' are required when mode='patch'"
                    summary = f"❌ Missing required parameters for patch mode"
                    return (error_msg, summary)
                
                # Get total line count of file, validate line numbers
                line_count_command = f"wc -l < {shlex.quote(file_path)}"
                returncode, line_count_output, stderr = await self.computer_client.exec_command(line_count_command)
                if returncode != 0:
                    error_msg = f"Error: Failed to get line count for '{file_path}': {line_count_output} {stderr}"
                    summary = f"❌ Failed to get line count ({file_path}): {line_count_output[:100]} {stderr[:100]}".replace('\n', ' ')
                    return (error_msg, summary)
                
                total_lines = int(line_count_output.strip())
                
                # Full boundary check
                if start_line < 1 or end_line < 1:
                    return (f"Error: Line numbers must be >= 1 (start={start_line}, end={end_line})",
                            f"❌ Invalid line range: lines must be >= 1")
                if start_line > end_line:
                    return (f"Error: start_line ({start_line}) > end_line ({end_line})",
                            f"❌ Invalid line range: start {start_line} > end {end_line}")
                if end_line > total_lines:
                    return (f"Error: end_line ({end_line}) exceeds total file lines ({total_lines})",
                            f"❌ Invalid line range: end line {end_line} > total lines {total_lines}")
                
                # Build ed script
                ed_script_lines = [
                    f"{start_line},{end_line}c"
                ]
                # Escape lines that start with '.' for ed
                for line in content.split('\n'):
                    if line.startswith('.'):
                        ed_script_lines.append('.' + line)
                    else:
                        ed_script_lines.append(line)
                ed_script_lines.extend([
                    ".",
                    "w",
                    "q"
                ])
                ed_script = '\n'.join(ed_script_lines)
                
                # Use ed with here-document
                quoted_file = shlex.quote(file_path)
                write_command = f"ed -s {quoted_file} << 'ED_EOF'\n{ed_script}\nED_EOF"
                
                # Execute command
                returncode, stdout, stderr = await self.computer_client.exec_command(write_command)
                
                if returncode != 0:
                    error_msg = f"Error: Failed to patch file '{file_path}': {stdout} {stderr}"
                    summary = f"❌ Failed to patch file ({file_path}): {stdout[:100]} {stderr[:100]}".replace('\n', ' ')
                    return (error_msg, summary)

            # Read file with line numbers
            read_command = f"cat -n {shlex.quote(file_path)}"
            returncode, stdout, stderr = await self.computer_client.exec_command(read_command)
            
            if returncode != 0:
                error_msg = f"Error: Failed to read file '{file_path}': {stdout} {stderr}"
                summary = f"❌ Failed to read file ({file_path}): {stdout[:100]} {stderr[:100]}".replace('\n', ' ')
                return (error_msg, summary)

            preview = stdout[:50].replace('\n', ' ') + '...' if len(stdout) > 50 else stdout.replace('\n', ' ')

            if mode == "patch":
                result = f"File patched successfully, updated content with line numbers:\n\n{stdout}"
                summary = f"✅ Successfully patched file ({file_path}): lines {start_line}-{end_line} replaced"
            else: # start mode
                result = f"File content with line numbers:\n\n{stdout}"
                summary = f"✅ Successfully read file with line numbers ({file_path}): {preview}"

            return (f"{result}\n\nNote: The above shows the file content with {'new' if mode == 'patch' else 'original'} precise line numbers.", summary)
        except Exception as e:
            error_msg = f"Error: Failed to patch file '{file_path}': {str(e)}"
            summary = f"❌ Failed to patch file ({file_path}): {str(e)[:100]}".replace('\n', ' ')
            return (error_msg, summary)
