"""
Impression recall tool that supports multi-dimensional query
"""
from typing import Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager, RecallImpressionsTool as ImpressMemRecallTool

from tools.media import AnalyzeImagesTool


class RecallImpressionsTool(Tool):
    """Tool for recalling memory impressions with multi-dimensional query support"""
    
    name = ImpressMemRecallTool.name
    
    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
        self.impressmem_tool = ImpressMemRecallTool(self.impression_manager)
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        definition = self.impressmem_tool.get_definition()
        
        definition["function"]["description"] += " Note: When image identification is needed and retrieved impressions contain reference images, " \
            + f"use {AnalyzeImagesTool.name} to compare the target against those references."
        
        return definition
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """
        Execute tool call and return result content
        
        Args:
            arguments: Tool arguments as JSON string
            
        Returns:
            Tuple of (full_result, summary)
        """
        return await self.impressmem_tool.execute(arguments)