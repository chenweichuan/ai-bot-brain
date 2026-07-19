import asyncio
import io
import requests
import re
import html
import chardet

from common.log import logger
from common.message import count_text_units
from common.proxy import ProxyClient
from providers.storage.client import StorageClient


class WebpageScraperClient:
    """Web page scraper module"""
    
    default_headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    }

    def __init__(self):
        self.proxy_client = ProxyClient.get_instance()

    async def scrape(self, url):
        """Get web page content"""
        logger.info(f"[WebScraper] get page: {url}")

        try:
            headers = self.default_headers.copy()
            # First, send a HEAD request to check content type without downloading body
            try:
                head_response = await self.proxy_client.request_by_proxy('HEAD', url, headers=headers, timeout=15)
                if not head_response:
                    head_response = await asyncio.to_thread(requests.head, url, headers=headers, timeout=15, allow_redirects=True)
                content_type = head_response.headers.get('Content-Type', '')
            except:
                content_type = ''
            
            # 尝试用代理流式请求
            async with self.proxy_client.stream_request_by_proxy('GET', url, headers=headers, timeout=15) as response:
                if response:
                    return await self._process_response(response, content_type, is_download=False)
                else:
                    # 代理不可用，使用 requests 回退
                    logger.info("[WebScraper] get page by proxy is failed, falling back to requests")
                    response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15, stream=True)
                    response.raise_for_status()
                    return await self._process_response(response, content_type, is_download=False)
                    
        except Exception as e:
            logger.info(f"[WebScraper] get page error: {e}")
            logger.exception(e)
            raise e

    async def download(self, url):
        """Download web page content"""
        logger.info(f"[WebScraper] download page: {url}")

        try:
            headers = self.default_headers.copy()
            
            # 尝试用代理流式请求
            async with self.proxy_client.stream_request_by_proxy('GET', url, headers=headers, timeout=15) as response:
                if response:
                    return await self._process_response(response, '', is_download=True)
                else:
                    # 代理不可用，使用 requests 回退
                    logger.info("[WebScraper] download page by proxy is failed, falling back to requests")
                    response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15, stream=True)
                    response.raise_for_status()
                    return await self._process_response(response, '', is_download=True)
                    
        except Exception as e:
            logger.info(f"[WebScraper] download page error: {e}")
            logger.exception(e)
            raise e

    def _is_text_content(self, content_type):
        """判断是否为文本内容"""
        text_types = ["text/", "application/json", "application/xml", "application/xhtml", 
                     "application/javascript", "application/ecmascript", "application/rss", 
                     "application/atom", "application/x-ndjson", "text/markdown"]
        return any(t in content_type for t in text_types)

    async def _read_stream(self, response, content_type):
        """读取流式数据，返回 (content, is_text)"""
        is_text = self._is_text_content(content_type)
        
        if hasattr(response, 'aiter_bytes'):
            if is_text:
                content = await response.aread()
                return content, True
            else:
                buffered = io.BytesIO()
                async for block in response.aiter_bytes(1024):
                    buffered.write(block)
                buffered.seek(0)
                return buffered, False
        else:
            if is_text:
                return response.content if hasattr(response, 'content') else response.text, True
            else:
                buffered = io.BytesIO()
                for block in response.iter_content(1024):
                    buffered.write(block)
                buffered.seek(0)
                return buffered, False

    async def _process_response(self, response, content_type, is_download=False):
        """统一处理响应"""
        content_type = response.headers.get('Content-Type', content_type)
        content, is_text = await self._read_stream(response, content_type)
        
        if is_download:
            if is_text:
                return content if isinstance(content, str) else content.decode('utf-8', errors='ignore')
            else:
                return content
        else:
            if is_text:
                if isinstance(content, bytes):
                    encoding = chardet.detect(content)['encoding']
                    if encoding and encoding.lower().startswith('gb'):
                        page_html = content.decode(encoding.lower(), errors='ignore')
                    else:
                        page_html = content.decode('utf-8', errors='ignore')
                else:
                    page_html = content
                return self._strip_tags(page_html)
            else:
                file_url = StorageClient.path_to_url(await StorageClient.save(content))
                if "video/" in content_type:
                    return f"!video[]({file_url})"
                elif "audio/" in content_type:
                    return f"!audio[]({file_url})"
                elif "image/" in content_type:
                    return f"![]({file_url})"
                else:
                    return f"File: []({file_url})"

    def _strip_tags(self, page_content):
        """Strip HTML tags and convert to plain text"""
        page_content = re.sub(r"\s+", " ", page_content)
        page_content = re.sub(r"<script[^<>]*?>.*?<\/script>|<style[^<>]*?>.*?<\/style>", "", page_content)
        page_content = re.sub(r"<!--.*?-->", "", page_content)
        page_content = re.sub(r"<h1[^<>]*?>", "## ", page_content)
        page_content = re.sub(r"<h2[^<>]*?>", "### ", page_content)
        page_content = re.sub(r"<h[3-9][^<>]*?>", "#### ", page_content)
        page_content = re.sub(r"<\/table>|<\/ul>|<\/h\d>|<\/p>|</figure>|<\/div>", "[[Enter]][[Enter]]", page_content)
        page_content = re.sub(r"<br?\/>|<\/tr>", "[[Enter]]", page_content)
        page_content = re.sub(r"<img[^<>]*?src=['\"]?((?:blob:)?http[^'\"\s<>]+)['\"]?[^<>]*?>", " ![](\\1) ", page_content)
        page_text = re.sub(r"<[^<>]*?>", " ", page_content)
        page_text = re.sub(r"\s+", " ", html.unescape(page_text))
        page_text = re.sub(r"\s*\[\[Enter\]\]\s*", "\n", page_text)
        page_text = re.sub(r"\n{3,}", "\n\n\n", page_text).strip()
        return page_text
