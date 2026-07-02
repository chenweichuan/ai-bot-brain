# encoding:utf-8
from typing import Optional

from common.log import logger
from config import conf
from common.content_moderation.enums import FileType, CheckResult, CheckStatus
from common.content_moderation.aliyun import AliyunContentModeration


# 文件扩展名映射
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif'}
AUDIO_EXTENSIONS = {'mp3', 'wav', 'aac', 'flac', 'm4a', 'ogg', 'wma', 'opus'}
VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv', 'webm', 'mts'}
DOCUMENT_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'md', 'html', 'htm'}


class ContentModerationClient:
    """内容审核客户端"""

    _instance: Optional['ContentModerationClient'] = None

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.enabled = False
        self.provider = None
        self.adapter = None
        self.fallback_action = "reject"  # reject 或 allow
        self.reject_log_only = False

        self._load_config()

    def _load_config(self):
        """加载配置"""
        config = conf().get("content_moderation", {})
        self.enabled = config.get("enabled", False)
        self.provider = config.get("provider", "aliyun")
        self.fallback_action = config.get("fallback_action", "reject")
        self.reject_log_only = config.get("reject_log_only", False)

        if not self.enabled:
            logger.info("[ContentModeration] 内容审核未启用")
            return

        if self.provider == "aliyun":
            aliyun_config = config.get("aliyun", {})
            self.adapter = AliyunContentModeration(aliyun_config)
            logger.info("[ContentModeration] 阿里云内容审核初始化成功")
        else:
            logger.warning(f"[ContentModeration] 不支持的审核服务商: {self.provider}")
            self.enabled = False

    def is_enabled(self) -> bool:
        """是否启用审核"""
        return self.enabled and self.adapter is not None

    @staticmethod
    def detect_file_type_from_url(url: str) -> FileType:
        """
        从URL识别文件类型

        Args:
            url: 文件公网URL

        Returns:
            FileType
        """
        # 通过URL路径判断
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
        审核文件

        Args:
            file_url: 文件公网URL

        Returns:
            CheckResult
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "审核未启用")

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
            return CheckResult(CheckStatus.PASS, "非审核文件类型，跳过")

    async def check_image(self, image_url: str) -> CheckResult:
        """
        审核图片

        Args:
            image_url: 图片公网URL
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "审核未启用")

        try:
            result = await self.adapter.check_image(image_url)
            self._log_result("图片", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] 图片审核异常: {e}")
            return self._get_fallback_result(str(e))

    async def check_audio(self, audio_url: str) -> CheckResult:
        """
        审核音频

        Args:
            audio_url: 音频公网URL
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "审核未启用")

        try:
            result = await self.adapter.check_audio(audio_url)
            self._log_result("音频", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] 音频审核异常: {e}")
            return self._get_fallback_result(str(e))

    async def check_video(self, video_url: str) -> CheckResult:
        """
        审核视频

        Args:
            video_url: 视频公网URL
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "审核未启用")

        try:
            result = await self.adapter.check_video(video_url)
            self._log_result("视频", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] 视频审核异常: {e}")
            return self._get_fallback_result(str(e))

    async def check_document(self, doc_url: str) -> CheckResult:
        """
        审核文档

        Args:
            doc_url: 文档公网URL
        """
        if not self.is_enabled():
            return CheckResult(CheckStatus.PASS, "审核未启用")

        try:
            result = await self.adapter.check_document(doc_url)
            self._log_result("文档", result)
            return self._apply_fallback(result)
        except Exception as e:
            logger.error(f"[ContentModeration] 文档审核异常: {e}")
            return self._get_fallback_result(str(e))

    def _log_result(self, file_type: str, result: CheckResult):
        """记录审核结果日志"""
        if result.is_pass():
            logger.info(f"[ContentModeration] {file_type}审核通过")
        elif result.is_block():
            logger.warning(f"[ContentModeration] {file_type}审核拒绝: {result.message}")
        elif result.is_review():
            logger.warning(f"[ContentModeration] {file_type}需人工复核: {result.message}")
        else:
            logger.error(f"[ContentModeration] {file_type}审核出错: {result.message}")

    def _apply_fallback(self, result: CheckResult) -> CheckResult:
        """应用降级策略"""
        if result.is_block() and self.reject_log_only:
            logger.warning(f"[ContentModeration] reject_log_only=true，只记录日志，不阻止")
            return CheckResult(CheckStatus.PASS, "降级通过（仅记录日志）")
        return result

    def _get_fallback_result(self, error_msg: str) -> CheckResult:
        """获取降级结果"""
        if self.fallback_action == "allow":
            logger.warning(f"[ContentModeration] 审核失败，降级通过: {error_msg}")
            return CheckResult(CheckStatus.PASS, f"降级通过: {error_msg}")
        else:
            logger.error(f"[ContentModeration] 审核失败，降级拒绝: {error_msg}")
            return CheckResult(CheckStatus.ERROR, f"审核失败: {error_msg}")


