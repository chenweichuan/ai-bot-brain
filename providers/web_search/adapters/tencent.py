import asyncio
import json
import math
import re
import time
from tencentcloud.common import credential
from tencentcloud.wsa.v20250508 import wsa_client, models

from common.log import logger
from config import conf


class TencentWebSearchApi:
    """Tencent web search API adapter"""

    def __init__(self):
        self.secret_id = conf().get("tencent_search_secret_id")
        self.secret_key = conf().get("tencent_search_secret_key")

    async def search(self, /, site=None, **args):
        """Search using Tencent web search API"""
        args["query"] = args["query"].strip()
        if not args["query"]:
            return []
        results = {}
        try:
            # params
            params = {
                "Query": args["query"],
                "Cnt": math.ceil(args["num"] / 10) * 10 if args.get("num") else 20,
            }
            site and params.update({"Site": site})
            if args["time_range"] == "within_1_day":
                params.update({"FromTime": int(time.time()) - 86400})
            elif args["time_range"] == "within_1_week":
                params.update({"FromTime": int(time.time()) - 604800})
            elif args["time_range"] == "within_1_month":
                params.update({"FromTime": int(time.time()) - 2592000})
            elif args["time_range"] == "within_1_year":
                params.update({"FromTime": int(time.time()) - 31536000})
            if params.get("FromTime"):
                params.update({"ToTime": int(time.time())})
            # logs
            logger.info(f"[Tencent] web search params: {params}")
            # request
            cred = credential.Credential(self.secret_id, self.secret_key)
            client = wsa_client.WsaClient(cred, "")
            req = models.SearchProRequest()
            req.from_json_string(json.dumps(params))
            resp = await asyncio.to_thread(client.SearchPro, req)
            results = json.loads(resp.to_json_string())
        except Exception as e:
            logger.info(f"[Tencent] web search error: {e}")
            logger.exception(e)
        # parse
        formatted_results = []
        try:
            for page in results["Pages"]:
                parsedPage = json.loads(page)
                formatted_result = {
                    "title": parsedPage.get("title", ""),
                    "brief": re.sub(r'<img[^<>]+?src=[\'"]?([^<>\'"\s]+)[\'"]?>?', r'![](\1)', parsedPage.get("passage", ""), flags=re.I),
                    "link": parsedPage["url"],
                    "time": parsedPage.get("date", ""),
                }
                for image_url in parsedPage.get("images", []):
                    formatted_result["brief"] += f" ![]({image_url})"
                formatted_results.append(formatted_result)
        except Exception as e:
            logger.info(f"[Tencent] web search error: {e} {results}")
            logger.exception(e)
        logger.info(f"[Tencent] web search results count: {len(formatted_results)}")
        return formatted_results


class TencentWebImageSearchApi:
    """Tencent web image search API adapter"""

    def __init__(self):
        self.secret_id = conf().get("tencent_search_secret_id")
        self.secret_key = conf().get("tencent_search_secret_key")

    async def search(self, /, site=None, **args):
        """Search using Tencent web image search API"""
        args["query"] = args["query"].strip()
        if not args["query"]:
            return []
        results = {}
        try:
            # params
            params = {"Query": args["query"]}
            site and params.update({"Site": site})
            if args["time_range"] == "within_1_day":
                params.update({"FromTime": int(time.time()) - 86400})
            elif args["time_range"] == "within_1_week":
                params.update({"FromTime": int(time.time()) - 604800})
            elif args["time_range"] == "within_1_month":
                params.update({"FromTime": int(time.time()) - 2592000})
            elif args["time_range"] == "within_1_year":
                params.update({"FromTime": int(time.time()) - 31536000})
            if params.get("FromTime"):
                params.update({"ToTime": int(time.time())})
            # logs
            logger.info(f"[Tencent] web image search params: {params}")
            # request
            cred = credential.Credential(self.secret_id, self.secret_key)
            client = wsa_client.WsaClient(cred, "")
            req = models.SearchProRequest()
            req.from_json_string(json.dumps(params))
            resp = await asyncio.to_thread(client.SearchPro, req)
            results = json.loads(resp.to_json_string())
        except Exception as e:
            logger.info(f"[Tencent] web image search error: {e}")
            logger.exception(e)
        # parse
        formatted_results = []
        try:
            for page in results["Pages"]:
                parsedPage = json.loads(page)
                formatted_result = {
                    "title": parsedPage.get("title", ""),
                    "brief": re.sub(r'<img[^<>]+?src=[\'"]?([^<>\'"\s]+)[\'"]?>?', r'![](\1)', parsedPage.get("passage", ""), flags=re.I),
                    "link": parsedPage["url"],
                    "time": parsedPage.get("date", ""),
                }
                for image_url in parsedPage.get("images", []):
                    formatted_result["brief"] += f" ![]({image_url})"
                formatted_results.append(formatted_result)
        except Exception as e:
            logger.info(f"[Tencent] web image search error: {e} {results}")
            logger.exception(e)
        logger.info(f"[Tencent] web image search results count: {len(formatted_results)}")
        return formatted_results