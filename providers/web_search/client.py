import asyncio
import re

from common.log import logger
from providers.short_link import ShortLinkClient
from .adapters import TencentWebSearchApi, TencentWebImageSearchApi, SogouWeixin, VolcWebSearchApi, VolcWebImageSearchApi


class WebSearchClient:
    """Web search module"""
    
    default_source_type = "general_search"

    source_type_search_ways = {
        default_source_type: [
            TencentWebSearchApi(),
            VolcWebSearchApi(),
        ],
        "wechat_official_account_articles": [
            SogouWeixin(),
        ],
        "image_search": [
            TencentWebImageSearchApi(),
            VolcWebImageSearchApi(),
        ],
    }

    time_ranges = ["within_1_day", "within_1_week", "within_1_month", "within_1_year", "no_limit"]

    default_search_args = {"query": "", "time_range": ""}

    def __init__(self):
        self.short_link_client = ShortLinkClient()

    async def search(self, source_type=None, num=None, **args):
        """Search the web"""
        args = {**self.default_search_args, **args}
        logger.info(f"[WebSearch] search args: {[source_type, args]}")
        if not args["query"]:
            return []
        if args["time_range"] == "no_limit":
            args["time_range"] = ""
        # Prepare query configuration
        search_ways = self.source_type_search_ways.get(source_type, self.source_type_search_ways[self.default_source_type])
        result_num = num or 50
        num_per_way = result_num // len(search_ways) + 1
        logger.info(f"[WebSearch] selected search ways: {[way.__class__.__name__ for way in search_ways]}")
        # Perform search
        multi_results = []
        for way_sequence in search_ways:
            way_sequence = way_sequence if isinstance(way_sequence, list) else [way_sequence]
            results = await self.search_by_way_sequence(way_sequence, num=num_per_way, **args)
            multi_results.append(results)
        multi_results = list(filter(lambda results: len(results) > 0, multi_results))
        # Mix results
        raw_mixed_results = self.mix_lists(*multi_results)
        mixed_results = []
        titles = []
        links = []
        for result in raw_mixed_results:
            # Basic deduplication
            if result["title"] in titles or result["link"] in links:
                continue
            mixed_results.append(result)
        mixed_results = mixed_results[0:result_num]
        # url to quote
        for result in mixed_results:
            image_urls = re.findall(r"!\[.*?\]\((.+?)\)", result["brief"])
            for image_url in image_urls:
                short_link = await self.short_link_client.convert_link_to_short(image_url)
                result["brief"] = result["brief"].replace(image_url, short_link)
            result["short_link"] = await self.short_link_client.convert_link_to_short(result["link"])
        # Return results
        logger.info(f"[WebSearch] search results: {mixed_results}")
        return mixed_results

    async def search_by_way_sequence(self, way_sequence, /, site=None, num=None, **args):
        """Search by way sequence"""
        way_sequence = way_sequence if isinstance(way_sequence, list) else [way_sequence]
        all_results = []

        for way in way_sequence:
            if isinstance(way, str) and way.startswith("site:"):
                site_way_sequence = self.source_type_search_ways.get(self.default_source_type)[0]
                site = way.replace("site:", "", 1)
                results = await self.search_by_way_sequence(
                    site_way_sequence, site=site, num=num, **{**args, "query": args["query"]}
                )
                all_results += results
            else:
                try:
                    results = await way.search(site=site, **args)
                    for result in results:
                        result["way"] = way.__class__.__name__
                        result["way"] += site and f":{site}" or ""
                    all_results += results
                except Exception as e:
                    logger.info(f"[WebSearch] search error: {e}")
                    logger.exception(e)
            # Stop backup search when accumulated results reach a certain ratio
            if len(all_results) > num * 2 / 3:
                break

        return all_results

    def mix_lists(self, *lists):
        """Mix multiple lists"""
        mixed_list = []
        for i in range(50):
            for list in lists:
                if len(list) > i:
                    mixed_list.append(list[i])
        return mixed_list

    def get_search_options(self):
        """Get search options"""
        source_types = list(self.source_type_search_ways.keys())
        return {
            "source_types": source_types,
            "time_ranges": self.time_ranges
        }