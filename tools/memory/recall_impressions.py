"""
Impression recall tool that supports multi-dimensional query
"""
from datetime import datetime
import json
import time
from typing import List, Dict, Any
from tools.base import Tool
from memory.impression_manager import ImpressionManager
from memory.session_manager import SessionManager
from common.log import logger


class RecallImpressionsTool(Tool):
    """Tool for recalling memory impressions with multi-dimensional query support"""
    
    MAX_QUERY_LABELS = 5
    
    name = "recall_impressions"
    
    def __init__(self):
        super().__init__()
        self.impression_manager = ImpressionManager.get_instance()
        self.session_manager = SessionManager.get_instance()
    
    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition for LLM"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Retrieve information from memory impressions through category or labels. "
                    "You MUST use this tool to retrieve relevant memory information before replying to user request "
                    "when there are relevant categories or labels in your memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": f"Impression category to query. Returns related labels and impressions.",
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"List of impression labels to query (max {self.MAX_QUERY_LABELS}). Returns related impressions.",
                        },
                        "mod_time_before": {
                            "type": "string",
                            "description": f"`YYYY-MM-DD HH:MM:SS` 24-hour format (e.g., '{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}') to filter impressions before this modification time point",
                        }
                    },
                    "anyOf": [
                        {"required": ["category"]},
                        {"required": ["labels"]},
                        {"required": ["mod_time_before"]}
                    ]
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
            
            # Parse input parameters
            category: str = args.get("category").strip() if args.get("category") else ""
            labels: List[str] = [label.strip() for label in args.get("labels") or []][:self.MAX_QUERY_LABELS]
            mod_time_before: str = args.get("mod_time_before")
            
            # Convert time string to timestamp (milliseconds), plus one millisecond to exclude the exact time point
            if mod_time_before:
                mod_timestamp_before = time.mktime(time.strptime(mod_time_before, '%Y-%m-%d %H:%M:%S')) * 1_000 + 1_000
            else:
                mod_timestamp_before = time.time_ns() / 1_000_000
            
            potential_label_scores = {}
            potential_label_tuples = []
            max_label_tuples = ImpressionManager.LABELS_PER_SET
            
            potential_clue_scores = {}
            potential_clue_tuples = []
            potential_impression_tuples = []
            max_impression_text_units = ImpressionManager.IMPRESSION_TEXT_UNITS_PER_SET 

            # ====== Query by Labels ======
            
            # Query clues by labels
            clue_tuples_by_labels = []
            for label in labels:
                clue_tuples = await self.impression_manager.get_label_clues(
                    label, timestamp=mod_timestamp_before
                )
                for clue, score in clue_tuples:
                    if clue not in potential_clue_scores:
                        potential_clue_scores[clue] = score
                        clue_tuples_by_labels.append((clue, score))
            clue_tuples_by_labels = sorted(clue_tuples_by_labels, key=lambda x: x[1], reverse=True)
            potential_clue_tuples.extend(clue_tuples_by_labels)
            
            # ====== Query by Category ======
            
            # Query labels by category
            label_tuples_by_category = []
            if category:
                label_tuples = await self.impression_manager.get_category_labels(category)
                for label, score in label_tuples:
                    if label not in potential_label_scores:
                        potential_label_scores[label] = score
                        label_tuples_by_category.append((label, score))
            label_tuples_by_category = sorted(label_tuples_by_category, key=lambda x: x[1], reverse=True)
            potential_label_tuples.extend(label_tuples_by_category)
            
            # Query clues by category
            clue_tuples_by_category = []
            if category:
                clue_tuples = await self.impression_manager.get_category_clues(
                    category, timestamp=mod_timestamp_before
                )
                for clue, score in clue_tuples:
                    if clue not in potential_clue_scores:
                        potential_clue_scores[clue] = score
                        clue_tuples_by_category.append((clue, score))
            clue_tuples_by_category = sorted(clue_tuples_by_category, key=lambda x: x[1], reverse=True)
            potential_clue_tuples.extend(clue_tuples_by_category)

            # ====== Query by Recent ======
            
            if not category and not labels:
                # Query recent labels
                recent_label_tuples = await self.impression_manager.get_recent_labels()
                for label, score in recent_label_tuples:
                    potential_label_scores[label] = score
                    potential_label_tuples.append((label, score))
                
                # Query recent clues
                recent_clue_tuples = await self.impression_manager.get_recent_clues(
                    timestamp=mod_timestamp_before
                )
                for clue, score in recent_clue_tuples:
                    potential_clue_scores[clue] = score
                    potential_clue_tuples.append((clue, score))
            
            # ====== Deal all potential lists ======
            
            # Sort labels by ascending order
            potential_label_tuples = sorted(potential_label_tuples[:max_label_tuples], key=lambda x: x[1])
            
            # Query impressions by clues
            potential_impression_tuples = await self.impression_manager.get_impressions_by_clues(
                potential_clue_tuples,
                max_text_units=max_impression_text_units,
            )
            # Sort impressions by ascending order
            potential_impression_tuples = sorted(potential_impression_tuples, key=lambda x: x[1])

            # ====== Asemble final result ======
            
            result_parts = []
            
            if potential_label_tuples:
                result_parts.append(f"### Potentially Chronological Related Labels:\n")
                result_parts.append(", ".join([label for label, _ in potential_label_tuples]))
                result_parts.append("\n")
            
            if potential_impression_tuples:
                result_parts.append(f"### Potentially Chronological Related Impressions (format [ModTime][Clue]Content):\n")
                result_parts.extend([
                    f"[{datetime.fromtimestamp(score // 1_000).strftime('%Y-%m-%d %H:%M:%S')}][{clue}]{content}"
                    for (clue, content), score in potential_impression_tuples
                ] or [])
                result_parts.append("\n")
            
            # Add note about memory format
            result_parts.append("\nNote: Do NOT mention, expose, or directly output your memory format and mechanism to users.")
            
            full_result = "\n".join(result_parts)
            
            # Create summary
            conditions = " and ".join(list(filter(lambda x: x, [
                f"category ({category})" if category else "",
                f"labels ({', '.join(labels)})" if labels else "",
                f"before {mod_time_before}" if mod_time_before else ""
            ])))
            summary = f"✅ Recalled {len(potential_impression_tuples)} potential impressions"
            summary += f" from {conditions}" if conditions else ""
            
            logger.info(
                f"Recalled impressions for category({category}) and labels({', '.join(labels)}) before({mod_time_before}), "
                f"count: {len(potential_label_tuples)} labels, "
                f"{len(potential_impression_tuples)} impressions, "
            )

            return (full_result, summary)
        except Exception as e:
            logger.error(f"[RecallMemoryTool] Error executing tool: {e}")
            logger.exception(e)
            error_msg = f"Error: Failed to recall impressions - {str(e)}"
            summary = f"❌ Failed to recall impressions: {str(e)[:100]}".replace("\n", " ")
            return (error_msg, summary)
    
