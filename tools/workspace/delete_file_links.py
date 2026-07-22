import json
import os
from typing import Dict, Any
from tools.base import Tool
from providers.computer.client import ComputerClient
from providers.storage.client import StorageClient
from common.log import logger
from providers.short_link.client import ShortLinkClient


class DeleteFileLinksTool(Tool):
    """Delete file links tool - delete short links and remove stored files"""
    
    name = "delete_file_links"
    
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
                "description": "Delete short links and their associated stored files. "
                    "Accepts short links or storage URLs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "links": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of short links or storage URLs to delete, maximum 10 per request.",
                            "maxItems": 10
                        },
                    },
                    "required": ["links"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute delete file links operation"""
        tool_args = json.loads(arguments)
        links = tool_args.get("links", [])
        
        if len(links) == 0:
            error_msg = "Error: No links provided."
            summary = "❌ No links provided"
            return (error_msg, summary)
        
        results = []
        success_count = 0
        fail_count = 0
        
        for link in links:
            try:
                link = link.strip()
                if not link:
                    continue

                # If it's a short link, resolve it to get the storage URL
                storage_url = link
                if link.startswith(self.shortlink_client.base_url):
                    token = link.rstrip("/").split("/")[-1]
                    resolved = await self.shortlink_client.get_link_by_token(token)
                    if resolved:
                        storage_url = resolved
                    else:
                        results.append({
                            "link": link,
                            "status": "failed",
                            "error": "Short link not found or already deleted"
                        })
                        fail_count += 1
                        continue

                # Delete the stored file first
                file_deleted = False
                if storage_url.startswith(StorageClient.base_url):
                    file_path = StorageClient.url_to_path(storage_url)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        file_deleted = True

                # Then delete the short link mapping
                await self.shortlink_client.delete_by_link(storage_url)

                results.append({
                    "link": link,
                    "status": "success",
                    "file_deleted": file_deleted
                })
                success_count += 1
            except Exception as e:
                logger.error(f"[DeleteFileLinks] Error processing {link}: {str(e)}")
                results.append({
                    "link": link,
                    "status": "failed",
                    "error": str(e)
                })
                fail_count += 1

        # Format result
        result_lines = ["File links deletion result:\n"]
        for idx, res in enumerate(results, 1):
            result_lines.append(f"{idx}. Link: {res['link']}")
            if res["status"] == "success":
                result_lines.append(f"   Status: ✅ Success")
                result_lines.append(f"   File deleted: {'Yes' if res['file_deleted'] else 'No'}\n")
            else:
                result_lines.append(f"   Status: ❌ Failed")
                result_lines.append(f"   Error: {res['error']}\n")
        
        result = "\n".join(result_lines)
        summary = f"{'✅' if not fail_count else '❌'} Deleted {success_count} links successfully"
        summary += f", {fail_count} failed" if fail_count > 0 else ""
        
        return (result, summary)