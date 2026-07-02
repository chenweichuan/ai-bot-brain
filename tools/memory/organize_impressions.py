"""
Impression organize tool for merging and organizing memory impression categories, labels and clues
"""
import json
from typing import List, Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager
from common.log import logger


class OrganizeImpressionsTool(Tool):
    """Tool for organizing and merging memory impressions at different levels"""
    
    name = "organize_impressions"
    
    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Organize and merge memory impressions at different levels (category, label, clue). "
                    "Use this to merge redundant categories, labels or clues, to clean up impression structure. "
                    "This helps maintain a clean and efficient impression system.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "string",
                            "enum": ["category", "label", "clue"],
                            "description": "The level of impression to organize: 'category' for merging categories, 'label' for merging labels, 'clue' for merging clues."
                        },
                        "from_items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of redundant item names to merge from. MUST be existing items in memory. For category level, this should contain exactly one item. For label and clue levels, this can contain multiple items to merge."
                        },
                        "to_item": {
                            "type": "string",
                            "description": "The target item name to merge into. MUST be existing item in memory. Cannot be the same as from_items. Will contain the merged content."
                        },
                        "new_content": {
                            "type": "string",
                            "description": "Optional new content for the merged item, ONLY applicable for 'clue' level. If not provided, will keep the existing content of the to_item or the content of the last item in from_items."
                        },
                        "reason": {
                            "type": "string",
                            "description": "Detailed reason for organizing the memory, explaining why the merge is needed."
                        },
                        "check": {
                            "type": "string",
                            "description": "Verification and analysis of the merge reason, confirming the merge is correct and necessary."
                        },
                        "is_redundant": {
                            "type": "boolean",
                            "description": "Confirm that all from_items and to_item are redundant.",
                            "default": False
                        },
                        "is_confirm": {
                            "type": "boolean",
                            "description": "Confirm the merge operation. Set to true to confirm, false to cancel.",
                            "default": False
                        }
                    },
                    "required": ["level", "from_items", "to_item", "reason", "check", "is_redundant", "is_confirm"],
                }
            }
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """
        Execute tool call and return result content
        
        Args:
            arguments: Tool arguments as JSON string
            
        Returns:
            Tuple of (full_result, summary) where:
                full_result: Complete tool result content as string
                summary: Simplified summary message for frontend display
        """
        try:
            args = json.loads(arguments) if arguments else {}
            
            # Extract required fields
            level: str = args.get("level", "").strip()
            from_items: List[str] = list(filter(None, [item.strip() for item in args.get("from_items") or []]))
            to_item: str = args.get("to_item", "").strip()
            new_content: str = args.get("new_content", "").strip()
            reason: str = args.get("reason", "").strip()
            check: str = args.get("check", "").strip()
            is_redundant: bool = args.get("is_redundant", False)
            is_confirm: bool = args.get("is_confirm", False)
            
            if level not in ["category", "label", "clue"]:
                error_msg = "Error: 'level' parameter must be one of 'category', 'label', 'clue'"
                summary = "❌ Invalid 'level' parameter"
                return (error_msg, summary)
            
            if level == "category" and len(from_items) != 1:
                error_msg = "Error: For 'category' level, 'from_items' must contain exactly one category name"
                summary = "❌ Invalid 'from_items' for category level"
                return (error_msg, summary)
            
            if "," in to_item:
                error_msg = "Error: 'to_item' parameter cannot contain comma"
                summary = "❌ 'to_item' cannot contain comma"
                return (error_msg, summary)
            
            if not list(filter(lambda x: x != to_item, from_items)):
                error_msg = "Error: 'from_items' and 'to_item' cannot be the same"
                summary = "❌ 'from_items' and 'to_item' cannot be the same"
                return (error_msg, summary)
            
            if not reason:
                error_msg = "Error: 'reason' parameter is required and cannot be empty"
                summary = "❌ 'reason' is required and cannot be empty"
                return (error_msg, summary)
            
            if not check:
                error_msg = "Error: 'check' parameter is required and cannot be empty"
                summary = "❌ 'check' is required and cannot be empty"
                return (error_msg, summary)
        
            if not is_redundant:
                result = f"⚠️ Merge skipped: {level} from '{', '.join(from_items)}' to {to_item}.\n"
                result += "Because not all items are redundant."
                summary = f"⚠️ Merge skipped because not all items are redundant"
                return (result, summary)
        
            if not is_confirm:
                result = f"⚠️ Merge not confirmed: {level} from '{', '.join(from_items)}' to {to_item}.\n"
                result += f"Reason: {reason}\n"
                result += f"Check: {check}\n"
                summary = f"⚠️ Merge not confirmed"
                return (result, summary)
        
            # Perform actual merge
            logger.debug(f"[OrganizeMemoryTool] Starting {level} merge: from {from_items} to {to_item}, reason: {reason}, check: {check}")
            
            if level == "category":
                result = await self.impression_manager.merge_categories(from_items[0], to_item)
            elif level == "label":
                result = await self.impression_manager.merge_labels(from_items, to_item)
            elif level == "clue":
                result = await self.impression_manager.merge_clues(from_items, to_item, new_content)
            
            result["reason"] = reason
            
            # Prepare result messages
            result_parts = []
            result_parts.append(f"### Memory Organization Completed Successfully:")
            
            result_parts.append(f"- Level: {level}")
            result_parts.append(f"- From: {', '.join(from_items)}")
            result_parts.append(f"- To: {to_item}")
            result_parts.append(f"- Reason: {reason}")
            result_parts.append(f"- Check: {check}")
            
            if level == "category":
                result_parts.append(f"- Labels moved: {result.get('labels_moved', 0)}")
                result_parts.append(f"- Clues moved: {result.get('clues_moved', 0)}")
                summary = f"✅ Merged category '{from_items[0]}' into '{to_item}'"
            elif level == "label":
                result_parts.append(f"- Clues moved: {result.get('clues_moved', 0)}")
                summary = f"✅ Merged labels '{', '.join(from_items)}' into '{to_item}'"
            elif level == "clue":
                result_parts.append(f"- Final content: {result.get('final_content', '')}")
                result_parts.append(f"- Messages moved: {result.get('messages_moved', 0)}")
                summary = f"✅ Merged {len(from_items)} clues into '{to_item}'"
            
            result_parts.append("\nNote: Do NOT mention, expose, or directly output your memory format and mechanism to users")
            
            full_result = "\n".join(result_parts)
            
            return (full_result, summary)
            
        except Exception as e:
            logger.error(f"[OrganizeMemoryTool] Error executing tool: {e}")
            logger.exception(e)
            error_msg = f"Error: Failed to organize impressions - {str(e)}"
            summary = f"❌ Failed to organize impressions: {str(e)[:100]}".replace("\n", " ")
            return (error_msg, summary)
