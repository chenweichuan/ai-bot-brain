"""
Tool manager - manages tool loading and execution
"""
import importlib
import pkgutil
from typing import Dict, List, Any
from common.log import logger

from .base import Tool


class ToolManager:
    """Tool manager that manages tools"""
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.tools = self._discover_tools()
        self.tool_map = {}

        for tool in self.tools:
            tool_name = tool.get_name()
            self.tool_map[tool_name] = tool
    
    async def get_definitions(self, filter: List[str] = None) -> List[Dict[str, Any]]:
        """
        Get tools definitions list
        """
        definitions = []
        for tool in self.tools:
            definition = await tool.get_definition()
            definitions.append(definition)
        
        return [d for d in definitions if filter is None or d["function"]["name"] in filter]
    
    async def execute(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool call and return tool message structure
        """
        tool_name = tool_call["function"]["name"]
        arguments = tool_call["function"]["arguments"]
        
        tool = self.tool_map.get(tool_name)

        result = {
            "content": "",
            "summary": "",
        }
        
        if tool:
            try:
                logger.info(f"[ToolManager] Executing tool: {tool_name} {arguments}")
                content, summary = await tool.execute(arguments)
                result["content"] = content
                result["summary"] = summary
            except Exception as e:
                logger.error(f"[ToolManager] Tool execution failed: {e}")
                error_msg = str(e)
                result["content"] = f"Failed: {error_msg}"
                result["summary"] = f"❌ Failed: {error_msg[:100]}".replace("\n", " ")
        else:
            logger.error(f"[ToolManager] Unknown tool: {tool_name}")
            result["content"] = f"Failed: Unknown tool {tool_name}"
            result["summary"] = f"❌ {result['content']}"

        return result

    def _discover_tools(self) -> List[Tool]:
        """
        自动发现并加载 tools 包中的所有工具
        自动发现所有子目录并加载其中的工具
        """
        tools = []
        
        # 导入 tools 包
        tools_package = importlib.import_module('tools')
        
        # 自动发现所有子目录
        for _, subdir, ispkg in pkgutil.iter_modules(tools_package.__path__):
            # 跳过非包目录和特殊目录
            if not ispkg or subdir.startswith('_'):
                continue

            # 导入子目录包
            subdir_package = importlib.import_module(f'tools.{subdir}')
            
            # 遍历子目录中的所有模块
            for _, module_name, _ in pkgutil.iter_modules(subdir_package.__path__):
                # 跳过 __init__.py
                if module_name == '__init__':
                    continue
                
                # 动态导入模块
                module = importlib.import_module(f'tools.{subdir}.{module_name}')
                
                # 查找模块中的 Tool 子类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    
                    # 检查是否是 Tool 的子类，且不是 Tool 本身
                    if (
                        isinstance(attr, type) and 
                        issubclass(attr, Tool) and 
                        attr != Tool and
                        not attr.__name__.startswith('_')
                    ):
                        tool_instance = attr()
                        tools.append(tool_instance)
                        logger.info(f"[ToolManager] Loaded tool: {attr.__name__} from {subdir}")
            
        logger.info(f"[ToolManager] Total tools loaded: {len(tools)}")
        
        return tools
