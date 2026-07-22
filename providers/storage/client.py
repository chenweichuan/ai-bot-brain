import io
import os
import re
import hashlib
import filetype
import httpx
import aiofiles
import asyncio
import shutil

from common.log import logger
from config import conf
from providers.content_moderation.client import ContentModerationClient


class StorageClient():
    base_url = conf().get("storage_base_url")
    base_path = conf().get("storage_base_path")

    @staticmethod
    async def save(file) -> str:
        """
        Save a file to storage directory.
        
        Args:
            file: Can be:
                - str: local file path, remote URL, or storage URL
                - bytes: file content
                - bytearray: file content
                - io.BytesIO: file content buffer
                
        Returns:
            str: Path to the saved file
        """
        # Return if already a storage URL or path
        if isinstance(file, str) and file.startswith(StorageClient.base_url):
            return StorageClient.url_to_path(file)
        elif isinstance(file, str) and file.startswith(StorageClient.base_path):
            return file

        original_filename = None
        # Handle different input types
        if isinstance(file, io.BytesIO):
            buffered = file.getvalue()
        elif isinstance(file, bytearray):
            buffered = bytes(file)
        elif isinstance(file, bytes):
            buffered = file
        elif isinstance(file, str) and file.startswith("http"):
            # Remote URL
            buffered = b""
            async with httpx.AsyncClient(follow_redirects=True) as client:
                remote = await client.get(file)
                buffered = remote.content
                # Get original filename from URL or Content-Disposition header
                original_filename = file.split('/')[-1].split('?')[0]
                if not original_filename:
                    content_disposition = remote.headers.get('Content-Disposition')
                    if content_disposition:
                        filename_match = re.findall(r'filename="?([^"]+)"?', content_disposition)
                        if filename_match:
                            original_filename = filename_match[0]
        else:
            # Local file path
            original_filename = file.split('/')[-1]
            async with aiofiles.open(file, 'rb') as f:
                buffered = await f.read()

        # Generate filename dir
        file_md5 = hashlib.md5(buffered).hexdigest()
        
        if original_filename:
            # Create directory structure: md5/original_filename
            dir_path = f"{StorageClient.base_path}/{file_md5}"
            file_path = f"{dir_path}/{original_filename}"
            # Create directory if not exists
            if not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
        else:
            # Fallback to original behavior
            file_kind = filetype.guess(buffered)
            if file_kind:
                file_ext = file_kind.extension
            else:
                # Check if it's text content
                try:
                    buffered.decode('utf-8')
                    # If no null bytes and can be decoded as UTF-8, treat as text
                    if b'\x00' not in buffered:
                        file_ext = "txt"
                    else:
                        file_ext = "bin"
                except UnicodeDecodeError:
                    file_ext = "bin"
            file_path = f"{StorageClient.base_path}/{file_md5}.{file_ext}"
        
        # Save to storage directory
        logger.info("[Storage] save file: {}".format(file_path))
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(buffered)

        # 异步审核文件
        asyncio.create_task(StorageClient._moderate_file_background(file_path))

        return file_path

    @staticmethod
    def path_to_url(path: str) -> str:
        """Convert storage path to URL"""
        path = os.path.normpath(path)
        if not path.startswith(StorageClient.base_path + os.sep):
            raise ValueError(f"Path is outside storage directory: {path}")
        return StorageClient.base_url + path.replace(StorageClient.base_path, '')

    @staticmethod
    def url_to_path(url: str) -> str:
        """Convert storage URL to path"""
        path = os.path.normpath(StorageClient.base_path + url.replace(StorageClient.base_url, ''))
        if not path.startswith(StorageClient.base_path + os.sep):
            raise ValueError(f"URL resolves to path outside storage directory: {url}")
        return path

    @staticmethod
    async def _moderate_file_background(file_path: str):
        """
        后台异步审核文件，如果有风险则删除文件
        
        Args:
            file_path: 文件的本地路径
        """
        file_url = StorageClient.path_to_url(file_path)
        
        try:
            moderation_client = ContentModerationClient.get_instance()
            if not moderation_client.is_enabled():
                return

            result = await moderation_client.check_file(file_url)
            if result.is_block():
                logger.warning(f"[Storage] 文件审核不通过，删除文件: {file_path}, 原因: {result.message}")
                try:
                    if os.path.exists(file_path):
                        # 规范化路径，避免路径格式差异导致的问题
                        normalized_base_path = os.path.normpath(StorageClient.base_path)
                        normalized_file_path = os.path.normpath(file_path)
                        
                        # 安全检查：确保文件路径在 base_path 下
                        if not normalized_file_path.startswith(normalized_base_path):
                            logger.error(f"[Storage] 文件路径不在安全目录内，拒绝删除: {file_path}")
                            return
                        
                        dir_path = os.path.dirname(normalized_file_path)
                        normalized_dir_path = os.path.normpath(dir_path)
                        
                        # 判断是否为我们创建的 md5 目录结构
                        # 条件：目录不是 base_path，且目录名是 32 位十六进制字符串（MD5格式）
                        dir_name = os.path.basename(normalized_dir_path)
                        is_md5_dir = (
                            normalized_dir_path != normalized_base_path and
                            len(dir_name) == 32 and
                            re.fullmatch(r'[0-9a-fA-F]{32}', dir_name) is not None
                        )
                        
                        if is_md5_dir:
                            # 确认目录在 base_path 下再删除
                            if normalized_dir_path.startswith(normalized_base_path):
                                shutil.rmtree(normalized_dir_path)
                                logger.info(f"[Storage] 已删除违规文件目录: {normalized_dir_path}")
                            else:
                                logger.error(f"[Storage] 目录路径不在安全目录内，拒绝删除: {normalized_dir_path}")
                        else:
                            # 只删除单个文件
                            os.remove(normalized_file_path)
                            logger.info(f"[Storage] 已删除违规文件: {normalized_file_path}")
                except Exception as e:
                    logger.error(f"[Storage] 删除违规文件失败: {file_path}, 错误: {e}")
            else:
                logger.info(f"[Storage] 文件审核通过: {file_path}")
        except Exception as e:
            logger.error(f"[Storage] 后台审核异常: {e}")
