"""
Skill manager - manage and provide access to skills
"""
import os
import yaml
import asyncio
from typing import List, Dict, Optional
from common.log import logger
import aiofiles

from config import conf
from providers.computer.client import ComputerClient


SKILL_MAIN_DOCUMENT = "SKILL.md"

class SkillManager:
    """Skill manager that manages skills"""
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.computer_client = ComputerClient.get_instance()

        self.skill_dir = self.computer_client.get_os_workspace() + "/skills"
        self.skills = []
        
        self.refresh_skills()

    def get_dir(self):
        return self.skill_dir

    def get_list(self, filter: List[str] = None) -> List[Dict[str, str]]:
        """
        获取所有技能的基本信息列表
        """
        return [s for s in self.skills if filter is None or s["name"] in filter]
    
    async def read_file(self, skill_name: str, skill_file: str = SKILL_MAIN_DOCUMENT) -> str:
        """
        获取指定技能文件的内容
        """
        # 拼装文件路径（约定skills下目录与skill name一致）
        skill_full_file_path = os.path.join(self.skill_dir, skill_name, skill_file)
        
        # 使用aiofiles异步读取文件（直接尝试读取，无需单独检查存在性）
        try:
            async with aiofiles.open(skill_full_file_path, "r", encoding="utf-8") as f:
                content = await f.read()
            logger.info(f"[SkillManager] Loaded skill file content: {skill_name}/{skill_file}")
            return content
        except FileNotFoundError:
            raise FileNotFoundError(f"Skill file not found: {skill_name}/{skill_file}")
        except Exception as e:
            logger.error(f"[SkillManager] Failed to read skill file: {e}")
            raise
    
    def refresh_skills(self) -> List[Dict[str, str]]:
        """
        自动加载所有技能的基本信息
        """
        skills = []
        
        try:
            # 只遍历dir下的一级子目录（不考虑多级嵌套）
            entries = os.scandir(self.skill_dir)
            for entry in entries:
                if entry.is_dir():
                    skill_name = entry.name
                    # 检查目录下是否有skill说明文件
                    skill_file_path = os.path.join(entry.path, SKILL_MAIN_DOCUMENT)
                    if os.path.exists(skill_file_path):
                        skill_info = self._parse_skill_info(skill_name)
                        if skill_info:
                            skills.append(skill_info)
                            logger.info(f"[SkillManager] Found skill: {skill_info['name']}")
            entries.close()
        except Exception as e:
            logger.error(f"[SkillManager] Failed to scan skills directory: {e}")
        
        self.skills = skills
        logger.info(f"[SkillManager] Found {len(skills)} skills")

    def _parse_skill_info(self, skill_name: str) -> Optional[Dict[str, str]]:
        """
        解析技能文件的YAML前置元数据
        """
        # 根据skill_name约定构造文件路径
        skill_path = os.path.join(self.skill_dir, skill_name, SKILL_MAIN_DOCUMENT)
        
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            description = ""
            try:
                # Try parse YAML front matter
                parts = content.split("---", 2)
                yaml_content = parts[1].strip()
                metadata = yaml.safe_load(yaml_content)
                description = metadata["description"]
            except Exception as e:
                parts = content.split("\n", 10)
                parts = list(filter(lambda x: x.strip(), parts))
                description = " | ".join(parts[:3])

            return {
                "name": skill_name,
                "description": description,
            } if description else None
        except FileNotFoundError:
            logger.error(f"[SkillManager] Skill file not found: {skill_path}")
            return None
        except Exception as e:
            logger.error(f"[SkillManager] Failed to parse skill info from {skill_path}: {e}")
            return None
