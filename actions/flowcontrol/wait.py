"""
Wait action
"""
from typing import Dict
from actions.base import Action


class WaitAction(Action):
    """Wait action"""
    
    name = "wait"
    
    def __init__(self):
        super().__init__()

    async def get_definition(self) -> Dict[str, str]:
        """Get action definition"""
        return {
            "name": self.name,
            "args": "",
            "description": "Wait until all previous actions are completed before continuing with subsequent output.",
        }
    
    async def execute(self, args: str) -> None:
        """Execute action"""
        pass
    