"""
Action manager - manages action loading and execution
"""
import importlib
import pkgutil
from typing import Dict, List, Any
from common.log import logger

from .base import Action


class ActionManager:
    """Action manager that manages actions"""
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.actions = self._discover_actions()
        self.action_map = {}

        for action in self.actions:
            action_name = action.get_name()
            self.action_map[action_name] = action
    
    async def get_definitions(self, filter: List[str] = None) -> List[Dict[str, str]]:
        """
        Get actions definitions list
        """
        definitions = []
        for action in self.actions:
            definition = await action.get_definition()
            definitions.append(definition)
        
        return [d for d in definitions if filter is None or d["name"] in filter]
    
    async def execute(self, action_call: dict[str, str]) -> None:
        """
        Execute an action call and return result
        """
        name = action_call.get("name")
        args = action_call.get("args")
        
        action = self.action_map.get(name)
        
        if action:
            try:
                logger.info(f"[ActionManager] Executing action: {name} {args}")
                await action.execute(args)
            except Exception as e:
                logger.error(f"[ActionManager] Action execution failed: {e}")
        else:
            logger.error(f"[ActionManager] Unknown action: {name}")

    def _discover_actions(self) -> List[Action]:
        """
        自动发现并加载 actions 包中的所有 action
        自动发现所有子目录并加载其中的 action
        """
        actions = []
        
        # 导入 actions 包
        actions_package = importlib.import_module('actions')
        
        # 自动发现所有子目录
        for _, subdir, ispkg in pkgutil.iter_modules(actions_package.__path__):
            # 跳过非包目录和特殊目录
            if not ispkg or subdir.startswith('_'):
                continue

            # 导入子目录包
            subdir_package = importlib.import_module(f'actions.{subdir}')
            
            # 遍历子目录中的所有模块
            for _, module_name, _ in pkgutil.iter_modules(subdir_package.__path__):
                # 跳过 __init__.py
                if module_name == '__init__':
                    continue
                
                # 动态导入模块
                module = importlib.import_module(f'actions.{subdir}.{module_name}')
                
                # 查找模块中的 Action 子类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    
                    # 检查是否是 Action 的子类，且不是 Action 本身
                    if (
                        isinstance(attr, type) and 
                        issubclass(attr, Action) and 
                        attr != Action and
                        not attr.__name__.startswith('_')
                    ):
                        action_instance = attr()
                        actions.append(action_instance)
                        logger.info(f"[ActionManager] Loaded action: {attr.__name__} from {subdir}")
            
        logger.info(f"[ActionManager] Total actions loaded: {len(actions)}")
        
        return actions