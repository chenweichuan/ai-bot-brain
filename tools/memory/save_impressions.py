"""
Memory save tool for saving multiple memory impressions in one batch call
"""
from typing import Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager
from impressmem.tools.save_impressions import SaveImpressionsTool as ImpressMemSaveTool


class SaveImpressionsTool(Tool):
    """Tool for saving multiple memory impressions at once"""

    name = ImpressMemSaveTool.name

    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
        self.impressmem_tool = ImpressMemSaveTool(self.impression_manager)

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        return self.impressmem_tool.get_definition()

    async def execute(self, arguments: str) -> tuple[str, str]:
        """
        Execute tool call and return result content

        Args:
            arguments: Tool arguments as JSON string

        Returns:
            Tuple of (full_result, summary)
        """
        return await self.impressmem_tool.execute(arguments)