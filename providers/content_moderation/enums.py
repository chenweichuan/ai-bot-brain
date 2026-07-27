# encoding:utf-8
from enum import Enum


class FileType(Enum):
    """File type"""
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    OTHER = "other"


class CheckStatus(Enum):
    """Check status"""
    PASS = "pass"          # pass
    BLOCK = "block"        # block
    REVIEW = "review"      # needs manual review
    ERROR = "error"        # check error
    TIMEOUT = "timeout"    # check timeout


class CheckResult:
    """Check result"""
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