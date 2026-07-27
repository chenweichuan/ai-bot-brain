# encoding:utf-8
from typing import Optional

from common.log import logger
from config import conf
from providers.content_moderation.enums import FileType, CheckResult, CheckStatus
from providers.content_moderation.aliyun import AliyunContentModeration


# File extension mappings
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif'}
AUDIO_EXTENSIONS = {'mp3', 'wav', 'aac', 'flac', 'm4a', 'ogg', 'wma', 'opus'}
VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv', 'webm', 'mts'}
DOCUMENT_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'md', 'html', 'htm'}


class ContentModerationClient:
    """Content moderation client"""

    _instance: Optional['ContentModerationClient'] = None

    @classmethod
    def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.enabled = False
        self.provider = None
        self.adapter = None
        self.fallback_action = "reject"  # reject or allow
        self.reject_log_only = False

        self._load_config()

    def _load_config(self):
        """Load configuration"""
        config = conf().get("content_moderation", {})
        self.enabled = config.get("enabled", False)
        self.provider = config.get("provider", "aliyun")
        self.fallback_action = config.get("fallback_action", "reject")
        self.reject_log_only = config.get("reject_log_only", False)

        if not self.enabled:
            logger.info("[ContentModeration] Content moderation is not enabled")
            return

        if self.provider == "aliyun":
            aliyun_config = config.get("aliyun", {})
            self.adapter = AliyunContentModeration(aliyun_config)
            logger.info("[ContentModeration] Aliyun content moderation initialized successfully")
        else:
            logger.warning(f"[ContentModeration] Unsupported moderation provider: {self.provider}")
            self.enabled = False

    def is_enabled(self) -> bool:
        """Check if moderation is enabled"""
        return self.enabled and self.adapter is not None

    @staticmethod
    def detect_file_type_from_url(url: str) -> FileType:
        """
        Detect file type from URL

        Args:
            url: Public URL of the file

        Returns:
            FileType
        """
        # Determine by URL path
        path_part = url.split('?')[0].split('#')[0]
        ext = path_part.split('.')[-1].lower() if '.' in path_part else ''
        if ext:
            if ext in IMAGE_EXTENSIONS:
                return FileType.IMAGE
            elif ext in AUDIO_EXTENSIONS:
                return FileType.AUDIO
            elif ext in VIDEO_EXTENSIONS:
                return FileType.VIDEO
            elif ext in DOCUMENT_EXTENSIONS:
                return FileType.DOCUMENT

        return FileType.OTHER

    async def check_file(self, file_url: str) -> CheckResult:
        """
        Moderate a file

        Args:
            file_url: Public URL of the file

        Returns:
            CheckResult
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "Moderation not enabled")

        file_type = self.detect_file_type_from_url(file_url)

        if file_type == FileType.IMAGE:
            return await self.check_image(file_url)
        elif file_type == FileType.AUDIO:
            return await self.check_audio(file_url)
        elif file_type == FileType.VIDEO:
            return await self.check_video(file_url)
        elif file_type == FileType.DOCUMENT:
            return await self.check_document(file_url)
        else:
            return CheckResult(CheckStatus.PASS, "Non-moderation file type, skipped")

    async def check_image(self, image_url: str) -> CheckResult:
        """
        Moderate an image

        Args:
            image_url: Public URL of the image
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "Moderation not enabled")

        try:
            result = await self.adapter.check_image(image_url)
            self._log_result("Image", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] Image moderation error: {e}")
            return self._get_fallback_result(str(e))

    async def check_audio(self, audio_url: str) -> CheckResult:
        """
        Moderate an audio file

        Args:
            audio_url: Public URL of the audio file
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "Moderation not enabled")

        try:
            result = await self.adapter.check_audio(audio_url)
            self._log_result("Audio", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] Audio moderation error: {e}")
            return self._get_fallback_result(str(e))

    async def check_video(self, video_url: str) -> CheckResult:
        """
        Moderate a video

        Args:
            video_url: Public URL of the video
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "Moderation not enabled")

        try:
            result = await self.adapter.check_video(video_url)
            self._log_result("Video", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] Video moderation error: {e}")
            return self._get_fallback_result(str(e))

    async def check_document(self, doc_url: str) -> CheckResult:
        """
        Moderate a document

        Args:
            doc_url: Public URL of the document
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "Moderation not enabled")

        try:
            result = await self.adapter.check_document(doc_url)
            self._log_result("Document", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] Document moderation error: {e}")
            return self._get_fallback_result(str(e))

    def _log_result(self, file_type: str, result: CheckResult):
        """Log moderation result"""
        if result.is_pass():
            logger.info(f"[ContentModeration] {file_type} moderation passed")
        elif result.is_block():
            logger.warning(f"[ContentModeration] {file_type} moderation blocked: {result.message}")
        elif result.is_review():
            logger.warning(f"[ContentModeration] {file_type} needs manual review: {result.message}")
        else:
            logger.error(f"[ContentModeration] {file_type} moderation error: {result.message}")

    def _apply_fallback(self, result: CheckResult) -> CheckResult:
        """Apply fallback strategy"""
        if result.is_block() and self.reject_log_only:
            logger.warning(f"[ContentModeration] reject_log_only=true, logging only, not blocking")
            return CheckResult(CheckStatus.PASS, "Fallback pass (log only)")
        return result

    def _get_fallback_result(self, error_msg: str) -> CheckResult:
        """Get fallback result"""
        if self.fallback_action == "allow":
            logger.warning(f"[ContentModeration] Moderation failed, fallback allow: {error_msg}")
            return CheckResult(CheckStatus.PASS, f"Fallback pass: {error_msg}")
        else:
            logger.error(f"[ContentModeration] Moderation failed, fallback reject: {error_msg}")
            return CheckResult(CheckStatus.ERROR, f"Moderation failed: {error_msg}")