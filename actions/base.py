"""
Base action class for all agent actions
"""
from abc import ABC, abstractmethod
from typing import Dict, Any


class Action(ABC):
    """Base class for all actions"""
    
    name = None
    
    def __init__(self):
        pass
    
    def get_name(self) -> str:
        """Get action name"""
        return self.name
    
    @abstractmethod
    async def get_definition(self) -> Dict[str, str]:
        """Get action definition for LLM"""
        pass
    
    @abstractmethod
    async def execute(self, args: str) -> None:
        """
        Execute action call and return result content
        
        Args:
            args: Action args as string
        """
        pass