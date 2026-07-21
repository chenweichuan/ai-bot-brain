"""
Skill reading tool
"""
import json
from typing import Dict, Any
from tools.base import Tool
from skills.manager import SKILL_MAIN_DOCUMENT, SkillManager
from common.log import logger


class ReadSkillTool(Tool):
    """Skill reading tool - allows agent to read and use specific skills"""
    
    name = "read_skill"
    
    def __init__(self):
        super().__init__()
        self.skill_manager = SkillManager.get_instance()
        self.skill_dir = self.skill_manager.get_dir()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        # Get skill list for enum options
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"Read a skill document under {self.skill_dir}. "
                    "MUST use this when you need to access domain-specific expertise or follow specialized procedures.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "The name of the skill to read.",
                        },
                        "skill_document": {
                            "type": "string",
                            "description": "The document path within the skill directory to read.",
                            "default": SKILL_MAIN_DOCUMENT
                        },
                    },
                    "required": ["skill_name", "skill_document"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute skill reading"""
        tool_args = json.loads(arguments)
        skill_name = tool_args.get("skill_name")
        skill_document = tool_args.get("skill_document", SKILL_MAIN_DOCUMENT)
        skill_dir = self.skill_manager.get_dir()

        try:
            self.skill_manager.refresh_skills()
            
            skill_content = await self.skill_manager.read_file(skill_name, skill_document)
            
            content = f"Document {skill_dir}/{skill_name}/{skill_document}:\n\n"
            content += f"{skill_content}\n\n"
            content += "Note: You should now use this file's knowledge to assist the user with their request. "
            content += f"You can execute the scripts mentioned in the skill, which are located relative to the path {skill_dir}/{skill_name}."
            
            summary = f"✅ Successfully read skill document: {skill_name}/{skill_document}"
        except FileNotFoundError as e:
            logger.error(f"[LoadSkillTool] File not found: {e}")
            content = f"File {skill_document} not found in skill {skill_name} directory"
            summary = f"❌ Failed to read skill document: File {skill_document} not found in {skill_name}"
        except Exception as e:
            logger.error(f"[LoadSkillTool] Failed to read file: {e}")
            content = f"Failed to read file: {str(e)}"
            summary = f"❌ Failed to read skill document: {str(e)[:100]}".replace("\n", " ")

        return (content, summary)
