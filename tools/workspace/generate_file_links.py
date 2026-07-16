import hashlib
import json
import os
from typing import Dict, Any, List
from tools.base import Tool
from providers.computer.client import ComputerClient
from common.storage import Storage
from common.log import logger
from providers.short_link.client import ShortLinkClient


class GenerateFileLinksTool(Tool):
    """Generate file links tool - copy files from workspace to storage and create short links in bulk"""
    
    name = "generate_file_links"
    
    def __init__(self):
        super().__init__()
        self.computer_client = ComputerClient.get_instance()
        self.shortlink_client = ShortLinkClient.get_instance()
        self.os_workspace = self.computer_client.get_os_workspace()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"Generate links for multiple files for easy sharing. "
                    "The generated links point to snapshotted copies of the target files. "
                    f"Access is restricted to: workspace ({self.os_workspace}) and /tmp directories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of file paths to generate links for, maximum 10 files per request.",
                            "maxItems": 10
                        },
                    },
                    "required": ["file_path"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute generate file links operation"""
        tool_args = json.loads(arguments)
        file_paths = tool_args.get("file_path", [])
        
        # Validate number of files
        if len(file_paths) == 0:
            error_msg = "Error: No file paths provided."
            summary = "❌ No file paths provided"
            return (error_msg, summary)
        
        results = []
        success_count = 0
        fail_count = 0
        
        for file_path in file_paths:
            try:
                # Resolve file path (handle relative paths)
                file_path = os.path.normpath(file_path)
                if not os.path.isabs(file_path):
                    file_path = os.path.normpath(os.path.join(self.os_workspace, file_path))

                # Validate file is within workspace
                if not file_path.startswith(self.os_workspace):
                    results.append({
                        "file_path": file_path,
                        "status": "failed",
                        "error": f"Access denied: The file MUST be in the workspace ({self.os_workspace}) or /tmp directory."
                    })
                    fail_count += 1
                    continue

                # Check if file exists
                if not os.path.exists(file_path):
                    results.append({
                        "file_path": file_path,
                        "status": "failed",
                        "error": "File does not exist"
                    })
                    fail_count += 1
                    continue

                # Check if it's a file (not directory)
                if not os.path.isfile(file_path):
                    results.append({
                        "file_path": file_path,
                        "status": "failed",
                        "error": "Not a file"
                    })
                    fail_count += 1
                    continue

                
                # Convert file path to storage URL
                storage_url = Storage.path_to_url(await Storage.save(file_path))
                
                # Generate short link
                short_link = await self.shortlink_client.convert_link_to_short(storage_url)
                
                # Get file info
                file_size = os.path.getsize(file_path)
                
                results.append({
                    "file_path": file_path,
                    "status": "success",
                    "file_size": file_size,
                    "short_link": short_link
                })
                success_count += 1
            except Exception as e:
                logger.error(f"[GenerateFileLinks] Error processing {file_path}: {str(e)}")
                results.append({
                    "file_path": file_path,
                    "status": "failed",
                    "error": str(e)
                })
                fail_count += 1

        # Format result
        result_lines = ["Snapshot file links generation result:\n"]
        for idx, res in enumerate(results, 1):
            result_lines.append(f"{idx}. File: {res['file_path']}")
            if res["status"] == "success":
                result_lines.append(f"   Status: ✅ Success")
                result_lines.append(f"   Size: {res['file_size']} bytes")
                result_lines.append(f"   Short Link: {res['short_link']}\n")
            else:
                result_lines.append(f"   Status: ❌ Failed")
                result_lines.append(f"   Error: {res['error']}\n")
        
        result = "\n".join(result_lines)
        summary = f"✅ Generated snapshot links for {success_count} files successfully"
        summary += f", {fail_count} files failed" if fail_count > 0 else ""
        
        return (result, summary)