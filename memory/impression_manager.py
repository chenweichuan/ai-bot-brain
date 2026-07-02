"""Impression Entry Manager
- High-density symbol system, time-based rolling
- Fully loaded into system context during each conversation
"""
import json
import re
import time
from typing import List, Dict, Any, Optional
from common.log import logger
from common.message import count_text_units, stringify_message
from config import conf
from providers.llm.client import LlmClient
from common.redis import RedisClient

from memory.context_builder import ContextBuilder


class ImpressionManager:
    """Impression Entry Manager"""
    _instance: Optional['ImpressionManager'] = None
    
    CATEGORIES_PER_SET = 500
    LABELS_PER_SET = 1500
    CLUES_PER_SET = 500
    MESSAGE_IDS_PER_SET: int = 500
    
    IMPRESSION_TEXT_UNITS_PER_SET: int = 15000
    MESSAGE_TEXT_UNITS_PER_SET: int = 30000

    UNPINNED_EMOJI = "⚪"
    PINNED_EMOJI = "📌"

    @classmethod
    def get_instance(cls) -> 'ImpressionManager':
        """
        Get singleton instance of ImpressionManager
        
        Returns:
            ImpressionManager instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.redis_client = RedisClient.get_instance()
        self.context_builder = ContextBuilder.get_instance()
        
        self.bot_name = conf().get("bot_name", "Bot")
        self.KEY_PREFIX = f"{self.bot_name.lower().replace(' ', '_')}:impression"
        
        self.RECENT_CATEGORY_ZSET_KEY = f"{self.KEY_PREFIX}:categories"
        self.RECENT_LABEL_ZSET_KEY = f"{self.KEY_PREFIX}:labels"
        self.RECENT_CLUE_ZSET_KEY = f"{self.KEY_PREFIX}:clues"
        self.PINNED_CLUE_ZSET_KEY = f"{self.KEY_PREFIX}:clues:pinned"
        
        self.CATEGORY_LABEL_ZSET_KEY = f"{self.KEY_PREFIX}:category:labels:%s"
        self.CATEGORY_CLUE_ZSET_KEY = f"{self.KEY_PREFIX}:category:clues:%s"
        
        self.LABEL_CLUE_ZSET_KEY = f"{self.KEY_PREFIX}:label:clues:%s"

        self.CLUE_MESSAGE_ID_ZSET_KEY = f"{self.KEY_PREFIX}:clue:message_ids:%s"

        self.IMPRESSION_CONTENT_KEY = f"{self.KEY_PREFIX}:content:%s"

    # ==================== Mixed Memory ====================
    
    async def get_mixed_labels(self, limit: int = LABELS_PER_SET) -> List[tuple[str, float]]:
        """
        Get mixed labels from recent labels and recent categories, sorted by time (newest first)
        """
        combined_labels = []
        combined_label_set = set()
        remaining_limit = limit
        
        # Get recent labels
        recent_labels = await self.get_recent_labels(remaining_limit // 5)
        combined_labels += recent_labels
        combined_label_set = combined_label_set.union({label for label, _ in recent_labels})
        remaining_limit -= len(recent_labels)
        
        # Get recent categories and their labels
        recent_categories = await self.get_recent_categories()
        category_labels_ref = {}
        for category, _ in recent_categories:
            if remaining_limit // 5 <= 0:
                break
            
            category_labels = await self.get_category_labels(category)
            category_labels = list(filter(lambda x: x[0] not in combined_label_set, category_labels))
            category_labels = category_labels[:remaining_limit // 5]
            if category_labels:
                category_labels_ref[category] = category_labels
                combined_labels += category_labels
                combined_label_set = combined_label_set.union({label for label, _ in category_labels})
                remaining_limit -= len(category_labels)
        
        logger.info(
            f"[ImpressionManager] Loaded mixed labels count: {len(combined_labels)}, "
            f"recent: {len(recent_labels)}, "
            f"categories: {', '.join([f'{category}({len(category_labels)})' for category, category_labels in category_labels_ref.items()])}"
        )
        
        return sorted(combined_labels, key=lambda x: x[1], reverse=True)

    async def get_mixed_impressions(
        self,
        max_text_units: int = IMPRESSION_TEXT_UNITS_PER_SET,
    ) -> List[tuple[str, (str, str), float]]:
        """
        Get global impression entries as (clue, content, score) tuples, sorted by time (newest first)
        """
        combined_impressions = []
        combined_clues = set()
        remaining_text_units = max_text_units
        
        # Get impressions for pinned clues, with a portion of the text units reserved for them to ensure they are always included
        pinned_clue_tuples = await self.get_pinned_clues()
        pinned_impressions = await self.get_impressions_by_clues(pinned_clue_tuples, remaining_text_units // 5)
        combined_impressions = [(self.PINNED_EMOJI, imp, score) for imp, score in pinned_impressions]
        combined_clues = combined_clues.union({clue for (clue, _), _ in pinned_impressions})
        remaining_text_units -= count_text_units(str([imp for imp, _ in pinned_impressions]))
        
        # Get impressions for recent clues, with a portion of the text units reserved for them to ensure they are always included
        recent_clue_tuples = await self.get_recent_clues()
        recent_clue_tuples = list(filter(lambda x: x[0] not in combined_clues, recent_clue_tuples))
        recent_impressions = await self.get_impressions_by_clues(recent_clue_tuples, remaining_text_units // 5)
        combined_impressions += [(self.UNPINNED_EMOJI, imp, score) for imp, score in recent_impressions]
        combined_clues = combined_clues.union({clue for (clue, _), _ in recent_impressions})
        remaining_text_units -= count_text_units(str([imp for imp, _ in recent_impressions]))
        
        # Get impressions for clues from recent categories, with remaining text units
        recent_category_tuples = await self.get_recent_categories()
        category_impressions_ref = {}
        for category, _ in recent_category_tuples:
            # Dynamically adjust estimated text units per impression
            estimated_text_units_per_impression = (max_text_units - remaining_text_units) // len(combined_impressions) \
                if combined_impressions else self.IMPRESSION_TEXT_UNITS_PER_SET // self.CLUES_PER_SET
                
            # Stop if not enough text units for the next impression
            if remaining_text_units // 5 < estimated_text_units_per_impression:
                break
            
            category_clue_tuples = await self.get_category_clues(category)
            category_clue_tuples = list(filter(lambda x: x[0] not in combined_clues, category_clue_tuples))
            category_impressions = await self.get_impressions_by_clues(category_clue_tuples, remaining_text_units // 5)
            if category_impressions:
                category_impressions_ref[category] = category_impressions
                combined_impressions += [(self.UNPINNED_EMOJI, imp, score) for imp, score in category_impressions]
                combined_clues = combined_clues.union({clue for (clue, _), _ in category_impressions})
                remaining_text_units -= count_text_units(str([imp for imp, _ in category_impressions])) if category_impressions else 0

        logger.info(
            f"[ImpressionManager] Loaded mixed impressions count: {len(combined_impressions)}, "
            f"pinned: {len(pinned_impressions)}, "
            f"recent: {len(recent_impressions)}, "
            f"categories: {', '.join([f'{category}({len(category_impressions)})' for category, category_impressions in category_impressions_ref.items()])}"
        )

        return sorted(combined_impressions, key=lambda x: x[2], reverse=True)

    # ==================== Recent Memory ====================

    async def get_recent_categories(self, limit: int = CATEGORIES_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited categories with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited categories sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.RECENT_CATEGORY_ZSET_KEY, timestamp, 0, start=0, num=limit, withscores=True)

    async def get_recent_labels(self, limit: int = LABELS_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited labels with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited labels sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.RECENT_LABEL_ZSET_KEY, timestamp, 0, start=0, num=limit, withscores=True)

    async def get_recent_clues(self, limit: int = CLUES_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited clues with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited clues sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.RECENT_CLUE_ZSET_KEY, timestamp, 0, start=0, num=limit, withscores=True)
    
    async def get_pinned_clues(self, limit: int = CLUES_PER_SET) -> List[tuple[str, float]]:
        """
        Get pinned clues with their scores (timestamps)
        """
        # Get all pinned clues sorted by newest first with scores
        return await self.redis_client.zrevrangebyscore(self.PINNED_CLUE_ZSET_KEY, float('inf'), 0, start=0, num=limit, withscores=True)

    # ==================== Category Memory ====================

    async def get_category_labels(self, category: str, limit: int = LABELS_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited labels for a given category with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited labels sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.CATEGORY_LABEL_ZSET_KEY % category, timestamp, 0, start=0, num=limit, withscores=True)
    
    async def get_category_clues(self, category: str, limit: int = CLUES_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited clues for a given category with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited clues sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.CATEGORY_CLUE_ZSET_KEY % category, timestamp, 0, start=0, num=limit, withscores=True)

    # ==================== Label Memory ====================

    async def get_label_clues(self, label: str, limit: int = CLUES_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited clues for a given label with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited clues sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.LABEL_CLUE_ZSET_KEY % label, timestamp, 0, start=0, num=limit, withscores=True)

    # ==================== Clue Memory ====================

    async def get_clue_message_ids(self, clue: str, limit: int = MESSAGE_IDS_PER_SET, timestamp: Optional[float] = None) -> List[tuple[List[str], float]]:
        """
        Get limited message IDs for a given clue with their scores (timestamps)
        """
        # Use current timestamp if not provided
        if timestamp is None:
            timestamp = time.time_ns() / 1_000_000
        
        # Get limited message IDs sorted by newest first with scores, before the given timestamp
        return await self.redis_client.zrevrangebyscore(self.CLUE_MESSAGE_ID_ZSET_KEY % clue, timestamp, 0, start=0, num=limit, withscores=True)

    async def get_impressions_by_clues(
        self,
        clues: List[tuple[str, float]] | List[str],
        max_text_units: int = IMPRESSION_TEXT_UNITS_PER_SET
    ) -> List[tuple[(str, str), float]]:
        """
        Get impression entries as (clue, content, score) tuples
        """
        impressions = []
        selected_impressions = []
        
        if not clues or max_text_units <= 0:
            return selected_impressions
        
        if clues and isinstance(clues[0], str):
            # Batch get all content keys in one Redis request
            content_keys = [self.IMPRESSION_CONTENT_KEY % clue for clue in clues]
            contents = await self.redis_client.mget(content_keys)
            
            # Pair clues with their contents and scores
            for clue, content in zip(clues, contents):
                if content:
                    content = content.decode("utf-8") if isinstance(content, bytes) else content
                    impressions.append((clue, content))
                    
            # Accumulate until we reach max_text_units
            total_units = 0
            for clue, content in impressions:
                units = count_text_units(f"[{clue}]{content}")
                
                if total_units + units > max_text_units:
                    break
                
                total_units += units
                selected_impressions.append((clue, content))
        elif clues:
            # Batch get all content keys in one Redis request
            content_keys = [self.IMPRESSION_CONTENT_KEY % clue for clue, _ in clues]
            contents = await self.redis_client.mget(content_keys)
            
            # Pair clues with their contents and scores
            for (clue, score), content in zip(clues, contents):
                if content:
                    content = content.decode("utf-8") if isinstance(content, bytes) else content
                    impressions.append(((clue, content), score))
        
            # Accumulate until we reach max_text_units
            total_units = 0
            for (clue, content), score in impressions:
                units = count_text_units(f"[{clue}]{content}")
                
                if total_units + units > max_text_units:
                    break
                    
                total_units += units
                selected_impressions.append(((clue, content), score))
        
        return selected_impressions

    async def get_messages_by_clues(self, clues: List[str], max_text_units: int = MESSAGE_TEXT_UNITS_PER_SET, timestamp: Optional[float] = None) -> List[tuple[str, float]]:
        """
        Get limited message sets for a given list of clues with their scores (timestamps)
        """
        from memory.session_manager import SessionManager
        
        session_manager = SessionManager.get_instance()
        
        combined_message_ids = []
        combined_message_id_set = set()
        selected_messages = []

        # Get message sets for each clue
        for clue in clues:
            message_ids = await self.get_clue_message_ids(clue, self.MESSAGE_IDS_PER_SET, timestamp)
            message_ids = list(filter(lambda x: x[0] not in combined_message_id_set, message_ids))
            combined_message_ids.extend(message_ids)
            combined_message_id_set.update([message_id[0] for message_id in message_ids])
        # Sort message sets by score (timestamp) in descending order
        combined_message_ids = sorted(combined_message_ids, key=lambda x: x[1], reverse=True)[:self.MESSAGE_IDS_PER_SET]
        
        # Get original messages for combined message IDs
        original_messages = await session_manager.multi_get_messages([message_id[0] for message_id in combined_message_ids])
        # Sort original messages by timestamp in descending order
        original_messages = sorted(original_messages, key=lambda x: x["timestamp"], reverse=True)
        
        # Accumulate until we reach max_text_units
        total_units = 0
        for message in original_messages:
            message_str = stringify_message(message)
            if message_str:
                units = count_text_units(message_str)
                if total_units + units > max_text_units:
                    break
                total_units += units
                selected_messages.append((message_str, message["timestamp"]))
        
        return selected_messages

    # ==================== Save Memory ====================

    async def save_clue_message_ids(self, clue: str, message_ids: List[str]) -> None:
        """
        Save a message IDs for a given clue with a timestamp score
        """
        clue = clue.strip().replace("\n", " ")
        message_ids = list(filter(lambda x: x, [message_id.strip().replace("\n", " ") for message_id in message_ids]))
        
        if not clue or not message_ids:
            logger.error(f"[ImpressionManager] Missing clue or message IDs")
            return
        
        # Use pipeline for atomic operations
        pipe = await self.redis_client.pipeline()
        
        for message_id in message_ids:
            # Use current timestamp as score
            score = time.time_ns() / 1_000_000
            # Save message IDs with score (timestamp)
            await pipe.zadd(self.CLUE_MESSAGE_ID_ZSET_KEY % clue, {message_id: score})
            
        # Remove old message IDs
        await pipe.zremrangebyrank(self.CLUE_MESSAGE_ID_ZSET_KEY % clue, 0, -10_001)
        
        # Execute pipeline
        await pipe.execute()
        
        logger.info(f"[ImpressionManager] Saved clue message IDs: [{clue}]{','.join(message_ids)}")

    async def save_impression(self, clue: str, content: str, category: str, labels: List[str], pin: bool = False) -> None:
        """
        Save impression entry - automatically determines whether to add new or update existing
        
        Args:
            clue: Clue for the impression
            content: Content corresponding to the clue
            pin: Whether to pin this impression (critical information that should never be removed)
        """
        clue = clue.strip().replace("\n", " ")
        content = content.strip().replace("\n", " ")
        category = category.strip().replace("\n", " ")
        labels = list(filter(lambda x: x, [label.strip().replace("\n", " ") for label in labels]))
        pin = pin == True or pin == self.PINNED_EMOJI

        if not clue or not content or not category or not labels:
            logger.error(f"[ImpressionManager] Missing clue, content, category or labels")
            return
            
        # Use current timestamp as score for both add and update operations
        score = time.time_ns() / 1_000_000
        
        # Check if the clue is new or existing
        is_new = await self.redis_client.exists(self.IMPRESSION_CONTENT_KEY % clue) == 0
        
        # Use pipeline for atomic operations
        pipe = await self.redis_client.pipeline()
        
        # Save recent category
        await pipe.zadd(self.RECENT_CATEGORY_ZSET_KEY, {category: score})
        await pipe.zremrangebyrank(self.RECENT_CATEGORY_ZSET_KEY, 0, -1_001)
        
        # Save recent labels
        for label in labels:
            await pipe.zadd(self.RECENT_LABEL_ZSET_KEY, {label: score})
            await pipe.zremrangebyrank(self.RECENT_LABEL_ZSET_KEY, 0, -10_001)
            
        # Save recent clues
        await pipe.zadd(self.RECENT_CLUE_ZSET_KEY, {clue: score})
        await pipe.zremrangebyrank(self.RECENT_CLUE_ZSET_KEY, 0, -100_001)
        
        # Add to or remove from pinned clues if needed
        if pin:
            await pipe.zadd(self.PINNED_CLUE_ZSET_KEY, {clue: score})
            await pipe.zremrangebyrank(self.PINNED_CLUE_ZSET_KEY, 0, -1_001)
        else:
            await pipe.zrem(self.PINNED_CLUE_ZSET_KEY, clue)
        
        # Save category labels
        for label in labels:
            await pipe.zadd(self.CATEGORY_LABEL_ZSET_KEY % category, {label: score})
            await pipe.zremrangebyrank(self.CATEGORY_LABEL_ZSET_KEY % category, 0, -10_001)
            
        # Save category clues
        await pipe.zadd(self.CATEGORY_CLUE_ZSET_KEY % category, {clue: score})
        await pipe.zremrangebyrank(self.CATEGORY_CLUE_ZSET_KEY % category, 0, -100_001)
        
        # Save label clues
        for label in labels:
            await pipe.zadd(self.LABEL_CLUE_ZSET_KEY % label, {clue: score})
            await pipe.zremrangebyrank(self.LABEL_CLUE_ZSET_KEY % label, 0, -100_001)
            
        # Save impression content in original raw format
        await pipe.set(self.IMPRESSION_CONTENT_KEY % clue, content)
        
        # Execute pipeline
        await pipe.execute()
        
        logger.info(
            f"[ImpressionManager] {'Added' if is_new else 'Updated'} impression: [{clue}]{content}[{category}][{','.join(labels)}]" + 
            (f" {self.PINNED_EMOJI} PINNED" if pin else "")
        )

    # ==================== Organize Memory ====================

    async def merge_categories(self, from_category: str, to_category: str) -> Dict[str, Any]:
        """
        Merge from_category into to_category, combining all related zsets using Redis native union operation
        
        Args:
            from_category: Source category to merge
            to_category: Target category to merge into
        
        Returns:
            Dictionary with merge result information: labels_moved, clues_moved
        """
        from_category = from_category.strip()
        to_category = to_category.strip()
        
        if not from_category or not to_category or from_category == to_category:
            raise ValueError(f"Invalid from_category or to_category: {from_category} -> {to_category}")
        
        # Get source zset keys
        from_label_key = self.CATEGORY_LABEL_ZSET_KEY % from_category
        from_clue_key = self.CATEGORY_CLUE_ZSET_KEY % from_category
        to_label_key = self.CATEGORY_LABEL_ZSET_KEY % to_category
        to_clue_key = self.CATEGORY_CLUE_ZSET_KEY % to_category
        
        # Count elements before merge
        to_labels_before = await self.redis_client.zcard(to_label_key)
        to_clues_before = await self.redis_client.zcard(to_clue_key)
        
        pipe = await self.redis_client.pipeline()
        
        # Union merge labels (take max score for duplicate elements)
        await pipe.zunionstore(
            to_label_key,
            [from_label_key, to_label_key],
            aggregate="MAX"
        )
        
        # Union merge clues
        await pipe.zunionstore(
            to_clue_key,
            [from_clue_key, to_clue_key],
            aggregate="MAX"
        )
        
        # Get the score of from_category in recent categories
        from_score = await self.redis_client.zscore(self.RECENT_CATEGORY_ZSET_KEY, from_category)
        if from_score is not None:
            to_score = await self.redis_client.zscore(self.RECENT_CATEGORY_ZSET_KEY, to_category) or 0
            # Update to_category score with max of from_category and existing score
            await pipe.zadd(self.RECENT_CATEGORY_ZSET_KEY, {to_category: max(from_score, to_score)})
            # Remove from_category from recent categories
            await pipe.zrem(self.RECENT_CATEGORY_ZSET_KEY, from_category)
        
        # Count elements after merge
        await pipe.zcard(to_label_key)
        await pipe.zcard(to_clue_key)
        
        # Execute pipeline
        results = await pipe.execute()
        to_labels_after = results[-2]
        to_clues_after = results[-1]
        
        # Calculate moved items
        labels_moved = to_labels_after - to_labels_before
        clues_moved = to_clues_after - to_clues_before
        
        logger.info(f"[ImpressionManager] Merged category {from_category} into {to_category}: moved {labels_moved} labels, {clues_moved} clues")
        return {
            "level": "category",
            "from": from_category,
            "to": to_category,
            "labels_moved": labels_moved,
            "clues_moved": clues_moved
        }
    
    async def merge_labels(self, from_labels: List[str], to_label: str) -> Dict[str, Any]:
        """
        Merge multiple from_labels into to_label, combining all related zsets using Redis native union operation
        
        Args:
            from_labels: List of source labels to merge
            to_label: Target label to merge into
        
        Returns:
            Dictionary with merge result information: clues_moved
        """
        to_label = to_label.strip()
        from_labels = list(filter(
            lambda label: label and label != to_label,
            [label.strip() for label in from_labels]
        ))
        
        if not from_labels or not to_label:
            raise ValueError(f"Invalid from_labels or to_label: {from_labels} -> {to_label}")
        
        # Get to_label clue zset keys
        to_clue_key = self.LABEL_CLUE_ZSET_KEY % to_label
        
        # Count clues before merge
        to_clues_before = await self.redis_client.zcard(to_clue_key)
        
        # Get recent categories and their label zset keys
        recent_categories = await self.get_recent_categories()
        category_keys = [self.CATEGORY_LABEL_ZSET_KEY % category for category, _ in recent_categories]
        
        pipe = await self.redis_client.pipeline()
        
        # Process each from label
        for from_label in from_labels:
            from_clue_key = self.LABEL_CLUE_ZSET_KEY % from_label
            
            # Union merge clues (take max score for duplicates)
            await pipe.zunionstore(
                to_clue_key,
                [from_clue_key, to_clue_key],
                aggregate="MAX"
            )
            
            # Get the score of from_label in recent labels
            from_score = await self.redis_client.zscore(self.RECENT_LABEL_ZSET_KEY, from_label)
            if from_score is not None:
                # Get the score of to_label in recent labels
                to_score = await self.redis_client.zscore(self.RECENT_LABEL_ZSET_KEY, to_label) or 0
                # Update to_label score with max of from_label and existing score
                await pipe.zadd(self.RECENT_LABEL_ZSET_KEY, {to_label: max(from_score, to_score)})
                # Remove from_label from recent labels
                await pipe.zrem(self.RECENT_LABEL_ZSET_KEY, from_label)
            
            # Find categories that have this label
            for key in category_keys:
                # Get the score of from_label in this category
                from_score = await self.redis_client.zscore(key, from_label)
                if from_score:
                    # Get the score of to_label in this category
                    to_score = await self.redis_client.zscore(key, to_label) or 0
                    # Add to_label to the category
                    await pipe.zadd(key, {to_label: max(to_score, from_score)})
                    # Remove from_label from the category
                    await pipe.zrem(key, from_label)
        
        # Count clues after merge
        await pipe.zcard(to_clue_key)
        
        # Execute pipeline
        results = await pipe.execute()
        to_clues_after = results[-1]
        
        # Calculate moved items
        clues_moved = to_clues_after - to_clues_before
        
        logger.info(f"[ImpressionManager] Merged labels {from_labels} into {to_label}: moved {clues_moved} clues")
        return {
            "level": "label",
            "from": from_labels,
            "to": to_label,
            "clues_moved": clues_moved,
        }
    
    async def merge_clues(self, from_clues: List[str], to_clue: str, new_content: str = "") -> Dict[str, Any]:
        """
        Merge multiple from_clues into to_clue, combining all related zsets using Redis native union operation
        
        Args:
            from_clues: List of source clues to merge
            to_clue: Target clue to merge into
            new_content: New content for the merged clue (if empty, will keep the content of to_clue)
        
        Returns:
            Dictionary with merge result information: new_content
        """
        to_clue = to_clue.strip()
        from_clues = list(filter(
            lambda clue: clue and clue != to_clue,
            [clue.strip() for clue in from_clues]
        ))
        
        if not from_clues or not to_clue:
            raise ValueError(f"Invalid from_clues or to_clue: {from_clues} -> {to_clue}")
        
        # Determine final content
        final_content = new_content \
            or await self.redis_client.get(self.IMPRESSION_CONTENT_KEY % to_clue) \
            or await self.redis_client.get(self.IMPRESSION_CONTENT_KEY % from_clues[-1])
        final_content = final_content.decode("utf-8") if isinstance(final_content, bytes) else final_content
        
        if not final_content:
            raise ValueError(f"No content found or new content for to_clue: {to_clue}")
        
        # Get to_clue message ids zset keys
        to_message_ids_key = self.CLUE_MESSAGE_ID_ZSET_KEY % to_clue
        
        # Count messages before merge
        to_messages_before = await self.redis_client.zcard(to_message_ids_key)
        
        # Get recent categories and their clue zset keys
        recent_categories = await self.get_recent_categories()
        category_keys = [self.CATEGORY_CLUE_ZSET_KEY % category for category, _ in recent_categories]
        
        # Get recent labels and their clue zset keys
        recent_labels = await self.get_recent_labels()
        label_keys = [self.LABEL_CLUE_ZSET_KEY % label for label, _ in recent_labels]
        
        pipe = await self.redis_client.pipeline()
        
        # Update the content of to_clue
        await pipe.set(self.IMPRESSION_CONTENT_KEY % to_clue, final_content)
        
        # Delete from_clues data
        for from_clue in from_clues:
            from_message_ids_key = self.CLUE_MESSAGE_ID_ZSET_KEY % from_clue
            
            # Union merge clues (take max score for duplicates)
            await pipe.zunionstore(
                to_message_ids_key,
                [from_message_ids_key, to_message_ids_key],
                aggregate="MAX"
            )
            
            
            # Get the score of from_clue in pinned clues
            from_score = await self.redis_client.zscore(self.PINNED_CLUE_ZSET_KEY, from_clue) or 0
            if from_score:
                # Get the score of to_clue in pinned clues
                to_score = await self.redis_client.zscore(self.PINNED_CLUE_ZSET_KEY, to_clue) or 0
                # Update to_clue score with max of from_clue and existing score
                await pipe.zadd(self.PINNED_CLUE_ZSET_KEY, {to_clue: max(to_score, from_score)})
                # Remove from pinned clues
                await pipe.zrem(self.PINNED_CLUE_ZSET_KEY, from_clue)
            
            # Get the score of from_clue in recent clues
            from_score = await self.redis_client.zscore(self.RECENT_CLUE_ZSET_KEY, from_clue) or 0
            if from_score:
                # Get the score of to_clue in recent clues
                to_score = await self.redis_client.zscore(self.RECENT_CLUE_ZSET_KEY, to_clue) or 0
                # Update to_clue score with max of from_clue and existing score
                await pipe.zadd(self.RECENT_CLUE_ZSET_KEY, {to_clue: max(to_score, from_score)})
                # Remove from recent clues
                await pipe.zrem(self.RECENT_CLUE_ZSET_KEY, from_clue)
            
            # Find categories that have this clue
            for category_key in category_keys:
                # Get the score of from_clue in this category
                from_score = await self.redis_client.zscore(category_key, from_clue) or 0
                if from_score:
                    # Get the score of to_clue in this category
                    to_score = await self.redis_client.zscore(category_key, to_clue) or 0
                    # Add to_clue to the category
                    await pipe.zadd(category_key, {to_clue: max(to_score, from_score)})
                    # Remove from_clue from the category
                    await pipe.zrem(category_key, from_clue)
        
            # Find labels that have this clue
            for label_key in label_keys:
                # Get the score of from_clue in this label
                from_score = await self.redis_client.zscore(label_key, from_clue) or 0
                if from_score:
                    # Get the score of to_clue in this label
                    to_score = await self.redis_client.zscore(label_key, to_clue) or 0
                    # Add to_clue to the label
                    await pipe.zadd(label_key, {to_clue: max(to_score, from_score)})
                    # Remove from_clue from the label
                    await pipe.zrem(label_key, from_clue)
        
        # Count messages after merge
        await pipe.zcard(to_message_ids_key)
        
        # Execute pipeline
        results = await pipe.execute()
        to_messages_after = results[-1]
        
        # Calculate moved messages
        messages_moved = to_messages_after - to_messages_before
        
        logger.info(f"[ImpressionManager] Merged {len(from_clues)} clues into {to_clue}")
        return {
            "level": "clue",
            "from": from_clues,
            "to": to_clue,
            "final_content": final_content,
            "messages_moved": messages_moved,
        }

    # ====================  Maintain Impressions By LLM ====================

    def slice_new_turn_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Slice the new turn-of-conversation messages from the history

        Args:
            history: Conversation history

        Returns:
            List of new turn-of-conversation messages
        """
        # Extract last bot message with previous bot message as context (include user messages in between if any)
        last_bot_idx = len(history) - 1 - next((i for i, msg in enumerate(reversed(history)) if msg["role"] == "assistant"), 0)
        prev_bot_idx = len(history[:last_bot_idx]) - 1 - next((i for i, msg in enumerate(reversed(history[:last_bot_idx])) if msg["role"] == "assistant"), 0)
        return history[prev_bot_idx:]

    async def maintain_impressions_by_llm(
        self,
        messages: List[Dict[str, Any]],
        username: str = None,
        instructions: str = "",
        model: str = conf().get("memory_model"),
    ) -> None:
        """
        Save impression entries based on the messages

        Args:
            messages: Conversation messages
            model: LLM model name to use
            instructions: Instructions for the LLM
            username: Username of the user
        """
        # Make LLM request to save impressions
        from tools.manager import ToolManager
        from tools.memory import SaveImpressionTool, OrganizeImpressionsTool
        
        tool_manager = ToolManager.get_instance()
        
        recent_categories = await self.get_recent_categories()
        mixed_labels = await self.get_mixed_labels()
        mixed_impressions = await self.get_mixed_impressions()

        # Get tool definition for save_memory
        send_tools = await tool_manager.get_definitions(filter=[
            SaveImpressionTool.name,
            OrganizeImpressionsTool.name,
        ])
        
        # Build context, limited messages
        send_messages = self.context_builder.build_context(
            history=messages,
            impression_categories=recent_categories,
            impression_labels=mixed_labels,
            impressions=mixed_impressions,
            instructions=instructions,
            tools=send_tools,
        )
        
        # If the last message is system or user, return
        if send_messages[-1]["role"] in ["system", "user"]:
            return
        
        send_messages.append({
            "role": "user",
            "content": (
                f"There are some new messages{f' with {username}' if username else ''}.\n"
                "- Analyze whether to add or update memory impressions base on the new messages.\n"
                "- Analyze whether to merge redundant or obsolete memory entries in all memory entries.\n"
                "Note: \n"
                f"- You SHOULD call {SaveImpressionTool.name} and {OrganizeImpressionsTool.name} tools at the same time.\n"
                "- This analysis and memory processing operation itself should NOT be recorded as a memory impression.\n"
                "- If there is nothing to do, just reply \"IGNORE\"."
            ),
        })
        
        request = {
            "messages": send_messages,
            "model": model,
            "thinking": True,
            "stream": False,
            "temperature": 0.1,
            "top_p": 0.85,
            "tools": send_tools,
            "tool_choice": "auto"
        }
        response = await LlmClient.factory(request["model"]).chat(**request)
        
        # Get the response message
        memory_message = response["choices"][0]["message"]
        
        # Execute each tool call
        for tool_call in memory_message.get("tool_calls") or []:
            try:
                # Execute tool call
                await tool_manager.execute(tool_call)
            except Exception as e:
                logger.error(f"[ImpressionManager] Failed to save impression: {e}")

        # Collect all memory tool calls from memory_message and new turn-of-conversation messages
        all_memory_tool_calls = memory_message.get("tool_calls") or []
        for message in messages:
            all_memory_tool_calls.extend([
                tc for tc in message.get("tool_calls") or []
                if tc["function"]["name"] == SaveImpressionTool.name
            ])

        # Save clue message IDs
        clue_message_ids = [msg["id"] for msg in messages]
        for tool_call in all_memory_tool_calls:
            try:
                arguments = tool_call["function"]["arguments"]
                args = json.loads(arguments) if arguments else {}
                clue: str = args.get("clue", "").strip()
                await self.save_clue_message_ids(clue, clue_message_ids)
            except Exception as e:
                logger.error(f"[ImpressionManager] Failed to save clue message IDs: {e}")
