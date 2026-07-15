"""
Impression recall tool that supports multi-dimensional query
"""
from typing import Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager
from impressmem.tools.recall_impressions import RecallImpressionsTool as ImpressMemRecallTool


class RecallImpressionsTool(Tool):
    """Tool for recalling memory impressions with multi-dimensional query support"""
    
    name = ImpressMemRecallTool.name
    
    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
        self.impressmem_tool = ImpressMemRecallTool(self.impression_manager)
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        return await self.impressmem_tool.get_definition()
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """
        Execute tool call and return result content
        
        Args:
            arguments: Tool arguments as JSON string
            
        Returns:
            Tuple of (full_result, summary)
        """
        return await self.impressmem_tool.execute(arguments)