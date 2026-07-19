"""
Fetch webpage content tool
"""
import json
from typing import Dict, Any
from common.message import count_text_units
from tools.base import Tool
from providers.web_scraper import WebpageScraperClient


class ScrapeWebpageTool(Tool):
    """Fetch webpage content tool"""
    
    name = "scrape_webpage"
    
    def __init__(self):
        super().__init__()
        self.web_scraper_client = WebpageScraperClient()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Fetch a webpage by URL and extract useful content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The webpage URL to be fetched.",
                        },
                    },
                    "required": ["url"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute webpage fetch"""
        tool_args = json.loads(arguments)
        url = tool_args.get("url")
        result = await self.web_scraper_client.scrape(url=url)
        
        content = ""

        # Extract content
        if result:
            # Truncate content if it exceeds the maximum number of text units
            max_content_units = 30000
            if count_text_units(result) > max_content_units:
                truncated_length = len(result) / count_text_units(result) * max_content_units * 0.9
                result = f"{result[:int(truncated_length/2)]}\n...[Content Truncated]...\n{result[-int(truncated_length/2):]}"
            content += f"Webpage {url} content:\n\n"
            content += f"{result}\n\n"
            content += "Note: Reply with reference to the fetched webpage content above."
            summary = f"✅ Successfully scraped webpage content from url ({url})"
        else:
            content = f"Failed to fetch webpage content from url ({url})"
            summary = f"❌ {content}"
        
        return (content, summary)
