"""
Memory save tool for saving memory impressions with specified fields
"""
import json
from typing import List, Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager
from common.log import logger


class SaveImpressionTool(Tool):
    """Tool for saving memory impression with specified fields"""
    
    MAX_LABELS = 5
    
    name = "save_impression"
    
    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Generate a memory impression that needs to be saved in an ultra-compact information-dense form "
                    "using an ultra-compact symbolic system, to serve as a contextual memory trace. "
                    "It must be able to help you recall the key information here. "
                    "Naming convention: clue uses UPPERCASE with hyphens; category and label use PascalCase. "
                    "Prioritize reusing and aligning with existing Clue, Category and Label sets. "
                    "No need to consider human readability.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clue": {
                            "type": "string",
                            "description": "Extremely concise, reusable identifier without time-of-day information, and should reasonably consider including user differentiation markers when appropriate. Give priority to merging or updating information for existing clues."
                        },
                        "content": {
                            "type": "string",
                            "description": "MUST be an ultra-compact information-dense form using an ultra-compact symbolic system."
                        },
                        "category": {
                            "type": "string",
                            "description": "High-level identifier, strictly prioritize the attributes of the content itself to match the corresponding domain classification, acting as the primary entry point for impression retrieval."
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"MUST be highly condensed core relevance identifiers, using universal terms with consistent naming, strictly focused on the most essential characteristics for accurate impression retrieval (max {self.MAX_LABELS})."
                        },
                        "pin": {
                            "type": "boolean",
                            "description": "Set to true ONLY for CRITICAL, PERMANENT information that must never be lost or purged. NEVER set pin to true for trivial, temporary, or non-critical information. USE EXTREMELY SPARINGLY!",
                            "default": False
                        }
                    },
                    "required": ["clue", "content", "category", "labels"],
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
            clue: str = args.get("clue", "").strip()
            content: str = args.get("content", "").strip()
            category: str = args.get("category", "").strip()
            labels: List[str] = list(filter(None, [label.strip() for label in args.get("labels") or []]))
            pin: bool = args.get("pin", False)
            
            # Validate required fields
            if not clue:
                error_msg = "Error: 'clue' parameter is required and cannot be empty"
                summary = "❌ 'clue' is required and cannot be empty"
                return (error_msg, summary)
            
            if not content:
                error_msg = "Error: 'content' parameter is required and cannot be empty"
                summary = "❌ 'content' is required and cannot be empty"
                return (error_msg, summary)
                
            if not category:
                error_msg = "Error: 'category' parameter is required and cannot be empty"
                summary = "❌ 'category' is required and cannot be empty"
                return (error_msg, summary)
                
            if not isinstance(labels, list) or len(labels) == 0:
                error_msg = "Error: 'labels' parameter must be a non-empty list of strings " \
                    "and contain at least one non-empty string"
                summary = "❌ 'labels' must be a non-empty list of strings"
                return (error_msg, summary)
                
            # Save the impression
            await self.impression_manager.save_impression(
                clue=clue,
                content=content,
                category=category,
                labels=labels,
                pin=pin
            )
            
            # Prepare result messages
            result_parts = []
            result_parts.append(f"### Memory Saved Successfully:")
            result_parts.append(f"- Clue: {clue}")
            result_parts.append(f"- Content: {content}")
            result_parts.append(f"- Category: {category}")
            result_parts.append(f"- Labels: {', '.join(labels)}")
            if pin:
                result_parts.append(f"- 📌 Pinned: Critical information permanently retained")
            result_parts.append("\n")
            result_parts.append("Note: Do NOT mention, expose, or directly output your memory format and mechanism to users")
            
            full_result = "\n".join(result_parts)
            summary = f"✅ Successfully saved impression to category ({category}) and labels ({', '.join(labels)})" + (" 📌 Pinned" if pin else "")
            
            return (full_result, summary)
            
        except Exception as e:
            logger.error(f"[SaveMemoryTool] Error executing tool: {e}")
            logger.exception(e)
            error_msg = f"Error: Failed to save impression - {str(e)}"
            summary = f"❌ Failed to save impression: {str(e)[:100]}".replace("\n", " ")
            return (error_msg, summary)
