"""
Skill list tool
"""
from typing import Dict, Any
from tools.base import Tool
from skills.manager import SkillManager
from common.log import logger


class ListSkillsTool(Tool):
    """Skill list tool - allows agent to list all available skills"""
    
    name = "list_skills"
    
    def __init__(self):
        super().__init__()
        self.skill_manager = SkillManager.get_instance()
        self.skill_dir = self.skill_manager.get_dir()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"Refresh and list all available skills under {self.skill_dir} with descriptions. "
                    "Use this to get the latest skill list or learn what each skill does before loading one.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute skill list"""
        try:
            # List all available skills
            self.skill_manager.refresh_skills()
            skills = self.skill_manager.get_list()
            
            if not skills:
                content = "No skills available at this time."
                summary = "ℹ️ No skills available at this time"
                return (content, summary)
            
            content = "Your skills:\n"
            content += "------\n"
            content += "\n".join(
                f"- {skill['name']}: {skill['description']}"
                for skill in skills
            )
            content += "\n------\n"
            
            summary = f"✅ Found {len(skills)} available skill(s)"
        except Exception as e:
            logger.error(f"[ListSkillsTool] Failed to list skills: {e}")
            content = f"Failed to list skills: {str(e)}"
            summary = f"❌ Failed to list skills: {str(e)[:100]}".replace("\n", " ")

        return (content, summary)