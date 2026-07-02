import asyncio
import requests
import urllib.parse
import re
import html

from common.log import logger
from common.proxy import ProxyClient


class SogouCookie:
    """Sogou cookie manager"""
    
    cookies = {}

    @staticmethod
    def save(cookies):
        SogouCookie.cookies.update(cookies)
        
    @staticmethod
    def get():
        return SogouCookie.cookies


class SogouMobile:
    """Sogou mobile search adapter"""
    
    base_url = "https://m.sogou.com/web/searchList.jsp"
    default_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Host": "m.sogou.com",
        "Referer": "https://m.sogou.com/",
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Mobile Safari/537.36"
    }
    
    tsn_map = {
        "within_1_day": 1,
        "within_1_week": 2,
        "within_1_month": 3,
        "within_1_year": 4,
    }

    def __init__(self):
        self.proxy_client = ProxyClient.get_instance()

    async def search(self, /, site=None, **args):
        """Search using Sogou mobile"""
        args["query"] = args["query"].strip()
        if not args["query"]:
            return []
        if site and args["time_range"]:
            return []
        result_html = ""
        try:
            # url
            query = args["query"] + (f" site:{site}" if site else "")
            tsn = (args["time_range"] in self.tsn_map) and self.tsn_map[args["time_range"]] or 0
            params = {"query": query, "tsn": tsn}
            encoded_params = urllib.parse.urlencode(params)
            # cookies
            cookies = SogouCookie.get()
            # logs
            logger.info(f"[Sogou] mobile search url: {self.base_url}?{encoded_params}")
            logger.info(f"[Sogou] mobile search params: {params}")
            logger.info(f"[Sogou] mobile search cookies: {cookies}")
            # request
            response = await self.proxy_client.get_by_proxy(self.base_url, params=encoded_params, headers=self.default_headers, cookies=cookies, timeout=15)
            if not response or response.text.find("此验证码用于确认这些请求是您的正常行为而不是自动程序发出的，需要您协助验证。") != -1:
                logger.info("[Sogou] mobile search by proxy is failed")
                response = await asyncio.to_thread(requests.get, self.base_url, params=encoded_params, headers=self.default_headers, cookies=cookies, timeout=15)
                SogouCookie.save(response.cookies.get_dict())
            response.raise_for_status()
            result_html = response.text
        except Exception as e:
            logger.info(f"[Sogou] mobile search error: {e}")
            logger.exception(e)
        # parse
        result_html = re.sub(r"\s+", " ", result_html)
        result_html = re.sub(r"<script[^<>]*?>.*?<\/script>|<style[^<>]*?>.*?<\/style>", "", result_html)
        result_elements = re.split(r"<[^<>]*?class=[^<>]*?vrResult[^<>]*?>", result_html)[1:-1]
        result_elements = list(filter(lambda result_element: result_element.find(" _relevant") == -1, result_elements))
        formatted_results = []
        for result_element in result_elements:
            try:
                result_link_element = (re.findall(r"<(?:span|a)[^<>]*?class=[^<>]*?resultLink[^<>]*?>.*?<\/a>", result_element) or [""])[0]
                title = re.sub(r"<[^<>]*?>", "", result_link_element)
                title = re.sub(r"\s+", " ", html.unescape(title)).strip()
                brief = result_element.replace(result_link_element, "")
                brief = re.sub(r"<br?\/>|<\/tr>|<\/table>|<\/ul>|<\/h\d>", "[[Enter]]", brief)
                brief = re.sub(r"<[^<>]*?>", " ", brief)
                brief = re.sub(r"\s+", " ", html.unescape(brief))
                brief = re.sub(r"(\s*\[\[Enter\]\]\s*)+", "\n", brief).strip()
                link = (re.findall(r".*[^\w]url=([^&]*).*", result_link_element) or [""])[0]
                link = urllib.parse.unquote(link).strip()
                time = (re.findall(r"(\d{4}-\d{2}-\d{2}|\d{1,2}.{1,2}前|.天)$", brief) or [""])[0]
                formatted_results.append({"title": title, "brief": brief, "link": link, "time": time})
            except Exception as e:
                logger.info(f"[Sogou] mobile search error: {e}")
                logger.exception(e)
        formatted_results = list(filter(lambda result_brief: result_brief["title"], formatted_results))
        formatted_results = list(filter(lambda result_brief: result_brief["title"].find("大家还在搜") == -1, formatted_results))
        formatted_results = list(filter(lambda result_brief: result_brief["title"].find(" - 相关") == -1, formatted_results))
        formatted_results = list(filter(lambda result_brief: result_brief["brief"], formatted_results))
        if not formatted_results:
            logger.info(f"[Sogou] mobile search error html: {result_html}")
        logger.info(f"[Sogou] mobile search results count: {len(formatted_results)}")
        return formatted_results


class SogouWeixin:
    """Sogou WeChat search adapter"""
    
    base_url = "https://weixin.sogou.com/weixinwap"
    default_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Host": "weixin.sogou.com",
        "Referer": "https://weixin.sogou.com/",
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Mobile Safari/537.36"
    }

    def __init__(self):
        self.proxy_client = ProxyClient.get_instance()

    async def search(self, **args):
        """Search using Sogou WeChat"""
        args["query"] = args["query"].strip()
        if not args["query"]:
            return []
        if args["time_range"]:
            return []
        result_html = ""
        try:
            # url
            params = {"query": args["query"], "type": 2}
            encoded_params = urllib.parse.urlencode(params)
            # cookies
            cookies = SogouCookie.get()
            # logs
            logger.info(f"[Sogou] weixin search url: {self.base_url}?{encoded_params}")
            logger.info(f"[Sogou] weixin search params: {params}")
            logger.info(f"[Sogou] weixin search cookies: {cookies}")
            # request
            response = await self.proxy_client.get_by_proxy(self.base_url, params=encoded_params, headers=self.default_headers, cookies=cookies, timeout=15)
            if not response or response.text.find("此验证码用于确认这些请求是您的正常行为而不是自动程序发出的，需要您协助验证。") != -1:
                logger.info("[Sogou] weixin search by proxy is failed")
                response = await asyncio.to_thread(requests.get, self.base_url, params=encoded_params, headers=self.default_headers, cookies=cookies, timeout=15)
                SogouCookie.save(response.cookies.get_dict())
            response.raise_for_status()
            result_html = response.text
        except Exception as e:
            logger.info(f"[Sogou] weixin search error: {e}")
            logger.exception(e)
        # parse
        result_html = re.sub(r"\s+", " ", result_html)
        result_html = re.sub(r"<script[^<>]*?>.*?<\/script>|<style[^<>]*?>.*?<\/style>", "", result_html)
        result_elements = re.split(r"<[^<>]*?id=[^<>]*?sogou_vr_\d+[^<>]*?>", result_html)[1:-1]
        formatted_results = []
        for resultElement in result_elements:
            try:
                result_link_element = (re.findall(r"<h4[^<>]*?>.*?<\/h4>", resultElement) or [""])[0]
                title = re.sub(r"<[^<>]*?>", "", result_link_element)
                title = re.sub(r"\s+", " ", html.unescape(title)).strip()
                brief = resultElement.replace(result_link_element, "")
                brief = re.sub(r"<[^<>]*?>", " ", brief)
                brief = re.sub(r"\s+", " ", html.unescape(brief)).strip()
                link = (re.findall(r".* href=['\"]?([^'\"]+?)['\"]? .*", result_link_element) or [""])[0]
                link = "https://weixin.sogou.com" + link.strip()
                time = (re.findall(r"(\d{4}-\d{2}-\d{2}|\d{1,2}.{1,2}前|.天)$", brief) or [""])[0]
                formatted_results.append({"title": title, "brief":brief, "link": link, "time": time})
            except Exception as e:
                logger.info(f"[Sogou] weixin search error: {e}")
                logger.exception(e)
        formatted_results = list(filter(lambda result_brief: result_brief["title"].find("大家还在搜") == -1, formatted_results))
        formatted_results = list(filter(lambda result_brief: result_brief["brief"], formatted_results))
        if not formatted_results:
            logger.info(f"[Sogou] weixin search error html: {result_html}")
        logger.info(f"[Sogou] weixin search results count: {len(formatted_results)}")
        return formatted_results
