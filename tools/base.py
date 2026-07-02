"""
Base tool class for all agent tools
"""
from abc import ABC, abstractmethod
from typing import Dict, Any


class Tool(ABC):
    """Base class for all tools"""
    
    name = None
    
    def __init__(self):
        pass
    
    def get_name(self) -> str:
        """Get tool name"""
        return self.name
    
    @abstractmethod
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        pass
    
    @abstractmethod
    async def execute(self, arguments: str) -> tuple[Any, str]:
        """
        Execute tool call and return result content
        
        Args:
            arguments: Tool arguments as JSON string
            
        Returns:
            Tuple of (full_result, summary) where:
                full_result: Complete tool result content as string
                summary: Simplified summary message for frontend display
        """
        pass
