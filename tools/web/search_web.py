"""
Web search tool
"""
import json
from typing import Dict, Any
from tools.base import Tool
from providers.web_search import WebSearchClient


class SearchWebTool(Tool):
    """Web search tool"""
    
    name = "search_web"
    
    def __init__(self):
        super().__init__()
        self.web_search_client = WebSearchClient()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        search_options = self.web_search_client.get_search_options()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Look up information via a web search engine.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_type": {
                            "type": "string",
                            "description": "Specify information source type (used only as a filter/selector, not as part of the query).",
                            "enum": search_options.get("source_types") or [],
                        },
                        "query": {
                            "type": "string",
                            "description": "Search keywords. Keep it very concise and human-like. Avoid filler words and complete sentences.",
                        },
                        "time_range": {
                            "type": "string",
                            "description": "Specify a time range.",
                            "enum": search_options.get("time_ranges") or [],
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute web search"""
        tool_args = json.loads(arguments)
        results = await self.web_search_client.search(**tool_args, num=30)
        
        content = ""

        # Format search results
        if isinstance(results, list):
            content += "Real-time web search results:\n\n"
            for result in results:
                content += f"- Title: [{result['title']}]({result['short_link']})\n"
                content += f"  Summary: {result['brief'].replace(' +', ' ')}\n"
                content += f"  Time: {result['time']}\n\n"
            content += "Note:\n"
            content += "- The above are real-time web search results you obtained via internet, which may include webpages, online images, online videos, etc.\n"
            content += "- You can cite Markdown of webpage links, online images, video cover images, etc. from above search results.\n"
            content += "- Where you use information from search results, you must label corresponding URL address by Markdown format.\n"
            content += "- Reply directly, do not mention the search process.\n"
            summary = f"✅ Found {len(results)} web search results for query: {tool_args.get('query')}"
        else:
            content = f"Web search failed for query: {tool_args.get('query')}"
            summary = f"❌ {content}"

        return (content, summary)
