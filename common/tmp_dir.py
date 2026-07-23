import io
import hashlib
import time
import aiofiles
import filetype
import httpx

from common.log import logger
from config import conf


class TmpDir(object):
    """A temporary directory that is deleted when the object is destroyed."""

    base_path = conf().get("tmp_base_path")

    @staticmethod
    def path():
        return TmpDir.base_path

    @staticmethod
    async def save(file) -> str:
        """
        Save a file to temporary directory.
        
        Args:
            file: Can be:
                - str: local file path, remote URL, or storage URL
                - bytes: file content
                - bytearray: file content
                - io.BytesIO: file content buffer
                
        Returns:
            str: Path to the saved temporary file
        """
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
        else:
            # Local file path
            async with aiofiles.open(file, 'rb') as f:
                buffered = await f.read()

        # Generate unique filename
        file_name = f"{str(int(time.time() * 1000))}-{hashlib.md5(buffered).hexdigest()}"
        file_kind = filetype.guess(buffered)
        file_ext = file_kind.extension if file_kind else "bin"
        file_path = f"{TmpDir.base_path}/{file_name}.{file_ext}"

        # Save to temporary directory
        logger.info("[TmpDir] save file: {}".format(file_path))
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(buffered)

        return file_path
