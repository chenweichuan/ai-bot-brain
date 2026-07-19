from typing import List, Optional
from common.log import logger
from config import conf

from providers.llm.client import LlmClient
from providers.t2i.client import T2IClient
from providers.speech import SpeechClient
from providers.web_search import WebSearchClient
from providers.web_scraper import WebpageScraperClient
from providers.short_link import ShortLinkClient
from providers.qrcode.client import QrcodeClient


class PrimitivesService:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        # 语音模块
        self.speech_client = SpeechClient.get_instance()
        
        # Web相关模块
        self.web_search_client = WebSearchClient()
        self.web_scraper_client = WebpageScraperClient()
        self.short_link_client = ShortLinkClient()
        
        # QRCode直接使用单例
        self.qrcode_client = QrcodeClient.get_instance()
        
        logger.info("[Primitives] Initialized")

    # ==================== LLM Primitives ====================

    async def chat(self, model: str = conf().get("chat_model"), **request):
        """
        LLM对话接口
        """
        model = model if model != "default" else conf().get("chat_model")
        logger.info(f"[Primitives] LLM chat: {model}")
        return await LlmClient.factory(model).chat(model=model, **request)

    # ==================== T2I Primitives ====================

    async def generate_image(self, model: str = conf().get("image_model"), **request):
        """
        T2I图片生成接口
        """
        model = model if model != "default" else conf().get("image_model")
        logger.info(f"[Primitives] T2I generate: {model}")
        return await T2IClient.factory(model).generate(**request)

    # ==================== Voice Primitives ====================

    async def speech_to_text(self, audio_file: str):
        """
        语音转文本
        """
        logger.info(f"[Primitives] Voice to text: {audio_file}")
        return await self.speech_client.speech_to_text(audio_file)

    async def text_to_speech(self, text: str, voice: Optional[str] = None):
        """
        文本转语音
        """
        logger.info(f"[Primitives] Text to voice: {text}, voice: {voice}")
        return await self.speech_client.text_to_speech(text)

    # ==================== Web Primitives ====================

    def get_search_options(self):
        """
        获取搜索选项
        """
        logger.info("[Primitives] Search options")
        return self.web_search_client.get_search_options()

    async def search_web(self, **params):
        """
        网页搜索
        """
        logger.info(f"[Primitives] Search web: {params}")
        return await self.web_search_client.search(**params)

    async def scrape_webpage(self, **params):
        """
        获取网页内容
        """
        logger.info(f"[Primitives] Web page: {params}")
        return await self.web_scraper_client.scrape(**params)

    async def download_webpage(self, **params):
        """
        下载网页
        """
        logger.info(f"[Primitives] Download page: {params}")
        return await self.web_scraper_client.download(**params)

    async def get_short_link(self, **params):
        """
        获取短链接
        """
        logger.info(f"[Primitives] Short link: {params}")
        return await self.short_link_client.convert_link_to_short(**params)

    async def get_link_by_token(self, **params):
        """
        根据token获取链接
        """
        logger.info(f"[Primitives] Get link by token: {params}")
        return await self.short_link_client.get_link_by_token(**params)

    # ==================== QRCode Primitives ====================

    async def generate_qrcode(self, data: str):
        """
        生成二维码
        """
        logger.info(f"[Primitives] Generate QRCode: {data}")
        return await self.qrcode_client.generate_qrcode(data)

    async def recognize_qrcode(self, image_file: str):
        """
        识别二维码
        """
        logger.info(f"[Primitives] Recognize QRCode: {image_file}")
        return await self.qrcode_client.recognize_qrcode(image_file)

