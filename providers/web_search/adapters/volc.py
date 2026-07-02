import asyncio
import json
import re
import aiohttp
from common.log import logger
from config import conf


class VolcWebSearchApi:
    """Volc web search API adapter"""
    
    api_url = "https://open.feedcoopapi.com/search_api/web_search"

    def __init__(self):
        self.api_key = conf().get("volc_search_api_key")

    async def search(self, /, site=None, **args):
        """Search using Volc web search API"""
        args["query"] = args["query"].strip()
        if not args["query"]:
            return []
        results = {}
        try:
            # params
            params = {
                "Query": args["query"],
                "SearchType": "web",
                "Count": args.get("num") or 20,
                "Filter": {
                    "NeedContent": True,
                    "NeedUrl": True,
                    "AuthInfoLevel": 1,
                },
            }
            site and params["Filter"].update({"Sites": site})
            # Handle time range
            time_range_mapping = {
                "within_1_day": "OneDay",
                "within_1_week": "OneWeek",
                "within_1_month": "OneMonth",
                "within_1_year": "OneYear"
            }
            if args.get("time_range") in time_range_mapping:
                params["TimeRange"] = time_range_mapping[args["time_range"]]
            # logs
            logger.info(f"[Volc] web search params: {params}")
            # request
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, headers=headers, json=params) as resp:
                    if resp.status == 200:
                        results = await resp.json()
                    else:
                        logger.error(f"[Volc] web search failed with status: {resp.status}")
        except Exception as e:
            logger.info(f"[Volc] web search error: {e}")
            logger.exception(e)
        # parse
        formatted_results = []
        try:
            web_results = results.get("Result", {}).get("WebResults", [])
            for item in web_results:
                formatted_result = {
                    "title": item.get("Title", ""),
                    "brief": item.get("Summary", "") or item.get("Snippet", ""),
                    "link": item.get("Url", ""),
                    "time": item.get("PublishTime", "").split("+")[0] if item.get("PublishTime") else "",
                }
                formatted_results.append(formatted_result)
        except Exception as e:
            logger.info(f"[Volc] web search parse error: {e} {results}")
            logger.exception(e)
        logger.info(f"[Volc] web search results count: {len(formatted_results)}")
        return formatted_results


class VolcWebImageSearchApi:
    """Volc web image search API adapter"""
    
    api_url = "https://open.feedcoopapi.com/search_api/web_search"

    def __init__(self):
        self.api_key = conf().get("volc_search_api_key")

    async def search(self, /, site=None, **args):
        """Search using Volc web image search API"""
        args["query"] = args["query"].strip()
        if not args["query"]:
            return []
        results = {}
        try:
            # params
            params = {
                "Query": args["query"],
                "SearchType": "image",
                "Count": 5,
            }
            # Handle time range
            time_range_mapping = {
                "within_1_day": "OneDay",
                "within_1_week": "OneWeek",
                "within_1_month": "OneMonth",
                "within_1_year": "OneYear"
            }
            if args.get("time_range") in time_range_mapping:
                params["TimeRange"] = time_range_mapping[args["time_range"]]
            # logs
            logger.info(f"[Volc] image search params: {params}")
            # request
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, headers=headers, json=params) as resp:
                    if resp.status == 200:
                        results = await resp.json()
                    else:
                        logger.error(f"[Volc] image search failed with status: {resp.status}")
        except Exception as e:
            logger.info(f"[Volc] image search error: {e}")
            logger.exception(e)
        # parse
        formatted_results = []
        try:
            image_results = results.get("Result", {}).get("ImageResults", [])
            for item in image_results:
                formatted_result = {
                    "title": item.get("Title", ""),
                    "brief": item.get("Title", ""),
                    "link": item.get("Url", "") or "#",
                    "time": item.get("PublishTime", "") if item.get("PublishTime") and not item.get("PublishTime").startswith("1970") else "",
                }
                # Add image URL to brief
                image_url = item.get("Image", {}).get("Url", "")
                if image_url:
                    formatted_result["brief"] += f" ![]({image_url})"
                formatted_results.append(formatted_result)
        except Exception as e:
            logger.info(f"[Volc] image search parse error: {e} {results}")
            logger.exception(e)
        logger.info(f"[Volc] image search results count: {len(formatted_results)}")
        return formatted_results