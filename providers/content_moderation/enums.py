# encoding:utf-8
from enum import Enum


class FileType(Enum):
    """文件类型"""
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    OTHER = "other"


class CheckStatus(Enum):
    """审核状态"""
    PASS = "pass"          # 通过
    BLOCK = "block"        # 拒绝
    REVIEW = "review"      # 需人工复核
    ERROR = "error"        # 审核出错
    TIMEOUT = "timeout"    # 审核超时


class CheckResult:
    """审核结果"""
    def __init__(self, status: CheckStatus, message: str = "", details: dict = None):
        self.status = status
        self.message = message
        self.details = details or {}

    def is_pass(self) -> bool:
        return self.status == CheckStatus.PASS

    def is_block(self) -> bool:
        return self.status == CheckStatus.BLOCK

    def is_review(self) -> bool:
        return self.status == CheckStatus.REVIEW

    def __repr__(self):
        return f"CheckResult(status={self.status}, message={self.message})"