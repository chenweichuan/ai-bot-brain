import hashlib
import json
import os
from typing import Dict, Any, List
from tools.base import Tool
from providers.computer.client import ComputerClient
from providers.storage.client import StorageClient
from common.log import logger
from common.tmp_dir import TmpDir
from providers.short_link.client import ShortLinkClient


class GenerateShortLinksTool(Tool):
    """Generate short links tool - snapshot host files to storage and create short links, or convert existing URLs to short links"""

    name = "generate_short_links"

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
                "description": f"Generate short links for host files or URLs. "
                    "file_paths: snapshots host files to immutable copies (original changes don't affect the link); "
                    "urls: pure redirects (no copy, target changes follow). "
                    f"Files restricted to workspace ({self.os_workspace}) or {TmpDir.path()}. Max 5 per array.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Host file paths to snapshot and generate short links for, max 5.",
                            "maxItems": 5
                        },
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Existing URLs to convert to short links, max 5.",
                            "maxItems": 5
                        },
                    },
                },
            },
        }

    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute generate short links operation"""
        tool_args = json.loads(arguments)
        file_paths = tool_args.get("file_paths", [])
        urls = tool_args.get("urls", [])

        if len(file_paths) == 0 and len(urls) == 0:
            error_msg = "Error: No file_paths or urls provided."
            summary = "❌ No file_paths or urls provided"
            return (error_msg, summary)

        results = []
        success_count = 0
        fail_count = 0

        # Process files (snapshot + short link)
        for file_path in file_paths:
            try:
                # Resolve file path (handle relative paths)
                file_path = os.path.normpath(file_path)
                if not os.path.isabs(file_path):
                    file_path = os.path.normpath(os.path.join(self.os_workspace, file_path))

                # Validate file is within workspace or tmp_dir
                if not file_path.startswith(self.os_workspace) and not file_path.startswith(TmpDir.path()):
                    results.append({
                        "source": file_path,
                        "type": "file",
                        "status": "failed",
                        "error": f"Access denied: The file MUST be in the workspace ({self.os_workspace}) or f{TmpDir.path()} directory."
                    })
                    fail_count += 1
                    continue

                # Check if file exists
                if not os.path.exists(file_path):
                    results.append({
                        "source": file_path,
                        "type": "file",
                        "status": "failed",
                        "error": "File does not exist"
                    })
                    fail_count += 1
                    continue

                # Check if it's a file (not directory)
                if not os.path.isfile(file_path):
                    results.append({
                        "source": file_path,
                        "type": "file",
                        "status": "failed",
                        "error": "Not a file"
                    })
                    fail_count += 1
                    continue

                # Convert file path to storage URL
                storage_url = StorageClient.path_to_url(await StorageClient.save(file_path))

                # Generate short link
                short_link = await self.shortlink_client.convert_link_to_short(storage_url)

                # Get file info
                file_size = os.path.getsize(file_path)

                results.append({
                    "source": file_path,
                    "type": "file",
                    "status": "success",
                    "file_size": file_size,
                    "short_link": short_link
                })
                success_count += 1
            except Exception as e:
                logger.error(f"[GenerateShortLinks] Error processing file {file_path}: {str(e)}")
                results.append({
                    "source": file_path,
                    "type": "file",
                    "status": "failed",
                    "error": str(e)
                })
                fail_count += 1

        # Process URLs (pure short link conversion)
        for url in urls:
            try:
                url = url.strip()
                if not url:
                    continue

                short_link = await self.shortlink_client.convert_link_to_short(url)

                results.append({
                    "source": url,
                    "type": "url",
                    "status": "success",
                    "short_link": short_link
                })
                success_count += 1
            except Exception as e:
                logger.error(f"[GenerateShortLinks] Error processing url {url}: {str(e)}")
                results.append({
                    "source": url,
                    "type": "url",
                    "status": "failed",
                    "error": str(e)
                })
                fail_count += 1

        # Format result
        result_lines = ["Short links generation result:\n"]
        for idx, res in enumerate(results, 1):
            type_label = res["type"].capitalize()
            result_lines.append(f"{idx}. {type_label}: {res['source']}")
            if res["status"] == "success":
                result_lines.append(f"   Status: ✅ Success")
                if res["type"] == "file":
                    result_lines.append(f"   Size: {res['file_size']} bytes")
                result_lines.append(f"   Short Link: {res['short_link']}\n")
            else:
                result_lines.append(f"   Status: ❌ Failed")
                result_lines.append(f"   Error: {res['error']}\n")

        result = "\n".join(result_lines)
        summary = f"{'✅' if not fail_count else '❌'} Generated {success_count} short links successfully"
        summary += f", {fail_count} failed" if fail_count > 0 else ""

        return (result, summary)