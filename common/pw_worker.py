import os
import asyncio
from typing import Dict, Optional
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

from common.log import logger
from config import conf


class PWWorker:
    """Playwright worker for rendering web pages"""
    
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.pw = None
        self.browser = None
        self.executable_path = conf().get("chromium_path", "/usr/bin/chromium-browser")
        self._init_lock = asyncio.Lock()

    async def ensure_browser(self):
        """懒加载浏览器"""
        async with self._init_lock:
            if self.browser is None:
                self.pw = await async_playwright().start()
                self.browser = await self.pw.chromium.launch(
                    headless=True,
                    executable_path=self.executable_path,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ]
                )

    async def clear_browser(self):
        """异常时清理浏览器"""
        async with self._init_lock:
            try:
                if self.browser:
                    await self.browser.close()
            except Exception as e:
                logger.warning(f"[PWWorker] close browser error: {e}")
            finally:
                self.browser = None
            
            try:
                if self.pw:
                    await self.pw.stop()
            except Exception as e:
                logger.warning(f"[PWWorker] stop playwright error: {e}")
            finally:
                self.pw = None

    async def fetch_html(self, url: str, proxy: Optional[Dict[str, str]] = None) -> str:
        """Fetch rendered HTML using Playwright"""
        TIMEOUT_MS = 30_000
        EXTRA_WAIT_MS = 5_000
        NETWORK_IDLE_TIMEOUT = 5_000

        try:
            await self.ensure_browser()
            
            context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                proxy=proxy,
            )
            
            page = await context.new_page()

            async def block_resources(route):
                resource_type = route.request.resource_type
                if resource_type in ["image", "font", "media", "stylesheet"]:
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", block_resources)

            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except PWTimeoutError:
                pass
            await page.wait_for_timeout(EXTRA_WAIT_MS)

            html = await page.content()
            
            await context.close()
            return html
            
        except Exception as e:
            logger.error(f"[PWWorker] fetch_html failed: {e}")
            
            # 清理可能残留的context
            try:
                if 'context' in locals() and context:
                    await context.close()
            except Exception:
                pass
            
            # 重启浏览器
            logger.info("[PWWorker] restarting browser...")
            try:
                await self.clear_browser()
            except Exception as restart_err:
                logger.error(f"[PWWorker] restart browser failed: {restart_err}")
            
            raise
