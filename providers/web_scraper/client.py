import asyncio
import io
import requests
import re
import html
import chardet

from common.log import logger
from common.message import count_text_units
from common.proxy import ProxyClient
from common.pw_worker import PWWorker
from common.storage import Storage


class WebpageScraperClient:
    """Web page scraper module"""
    
    default_headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    }

    def __init__(self):
        self.proxy_client = ProxyClient.get_instance()
        self._pw_worker = PWWorker.get_instance()

    async def scrape(self, url):
        """Get web page content"""
        logger.info(f"[WebScraper] get page: {url}")
        page_content = ""

        try:
            headers = self.default_headers.copy()
            # request
            response = await self.proxy_client.get_by_proxy(url, headers=headers, timeout=15)
            if not response:
                logger.info("[WebScraper] get page by proxy is failed")
                response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
            response.raise_for_status()
            
            if "image/" in response.headers.get('Content-Type', ''):
                buffered = io.BytesIO()
                for block in response.iter_content(1024):
                    buffered.write(block)
                buffered.seek(0)
                file_url = Storage.path_to_url(await Storage.save(buffered))
                page_content = f"图片：![]({file_url})"
            else:
                encoding = chardet.detect(response.content)['encoding']
                if encoding and encoding.lower().startswith('gb'):
                    page_html = response.content.decode(encoding.lower(), errors='ignore')
                else:
                    page_html = response.text
                page_content = self._strip_tags(page_html)
                # If direct scraping fails, try using Playwright to render the page
                if count_text_units(page_content) < 300:
                    try:
                        rendered_html = await self._pw_worker.fetch_html(url)
                        rendered_content = self._strip_tags(rendered_html)
                        if count_text_units(rendered_content) > count_text_units(page_content):
                            page_content = rendered_content
                    except Exception as e:
                        logger.info(f"[WebScraper] get page error: {e}")
                        logger.exception(e)
        except Exception as e:
            logger.info(f"[WebScraper] get page error: {e}")
            logger.exception(e)
            raise e

        return page_content

    async def download(self, url):
        """Download web page content"""
        logger.info(f"[WebScraper] download page: {url}")
        page_content = ""

        try:
            headers = self.default_headers.copy()
            # request
            response = await self.proxy_client.get_by_proxy(url, headers=headers, timeout=15)
            if not response:
                logger.info("[WebScraper] download page by proxy is failed")
                response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
            response.raise_for_status()
            
            if "image/" in response.headers.get('Content-Type', ''):
                buffered = io.BytesIO()
                for block in response.iter_content(1024):
                    buffered.write(block)
                buffered.seek(0)
                page_content = buffered
            else:
                page_content = response.text
                text_units = count_text_units(self._strip_tags(page_content))
                # If direct scraping fails, try using Playwright to render the page
                if text_units < 300:
                    try:
                        rendered_content = await self._pw_worker.fetch_html(url)
                        if count_text_units(self._strip_tags(rendered_content)) > text_units:
                            page_content = rendered_content
                    except Exception as e:
                        logger.info(f"[WebScraper] get page error: {e}")
                        logger.exception(e)
        except Exception as e:
            logger.info(f"[WebScraper] download page error: {e}")
            logger.exception(e)
            raise e

        return page_content

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
