import os
import asyncio
from common.log import logger


async def compress_video(input_path: str, target_size_mb: int, purpose: str = "llm") -> None:
    """
    压缩视频到指定大小（MB），直接覆盖原文件
    抛出异常表示压缩失败
    """
    # Validate input file exists
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    target_size_bytes = target_size_mb * 1024 * 1024
    input_size = os.path.getsize(input_path)
    
    if input_size <= target_size_bytes:
        logger.info(f"Video already under target size: {input_size / 1024 / 1024:.2f}MB <= {target_size_mb}MB")
        return
    
    logger.info(f"Compressing video: {input_path}, size: {input_size / 1024 / 1024:.2f}MB -> {target_size_mb}MB")
    
    # Get video duration
    duration_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path
    ]
    duration_proc = await asyncio.create_subprocess_exec(
        *duration_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    duration_stdout, duration_stderr = await duration_proc.communicate()
    if duration_proc.returncode != 0:
        raise RuntimeError(f"Failed to get video duration: {duration_stderr.decode()}")
    
    duration_str = duration_stdout.decode().strip()
    if not duration_str:
        raise RuntimeError("Invalid video duration (empty output)")
    
    duration = float(duration_str)
    if duration <= 0:
        raise RuntimeError(f"Invalid video duration: {duration}")
    
    # Multiple pass compression to hit target size
    max_attempts = 3
    current_bitrate_factor = 0.85  # Start with 85% of theoretical
    current_input_path = input_path
    temp_files = []
    
    try:
        for attempt in range(max_attempts):
            # Calculate target bitrate with current factor
            target_bitrate = int((target_size_bytes * current_bitrate_factor * 8) / duration)
            logger.info(f"Compression attempt {attempt + 1}/{max_attempts}, target bitrate: {target_bitrate} bps")
            
            # Generate temp output path for this attempt
            ext = os.path.splitext(input_path)[1]
            temp_output_path = input_path.replace(ext, f"_temp{attempt}{ext}")
            temp_files.append(temp_output_path)
            
            # Compress video with specified video codec
            compress_cmd = [
                "ffmpeg",
                "-i", current_input_path,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-r", "25",
                "-b:v", str(target_bitrate),
                "-bufsize", str(target_bitrate * 2),
                "-maxrate", str(int(target_bitrate * 1.5)),
                "-c:a", "aac",
                "-b:a", "64k",
                "-y",
                "-vf", "scale='min(1280,iw)':-2",
                temp_output_path
            ] if purpose == "llm" else [
                "ffmpeg",
                "-i", current_input_path,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-b:v", str(target_bitrate),
                "-bufsize", str(target_bitrate * 2),
                "-maxrate", str(int(target_bitrate * 1.5)),
                "-c:a", "aac",
                "-b:a", "128k",
                "-y",
                "-vf", "scale='min(1280,iw)':-2",
                temp_output_path
            ]
            
            compress_proc = await asyncio.create_subprocess_exec(
                *compress_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, compress_stderr = await compress_proc.communicate()
            
            if compress_proc.returncode != 0:
                raise RuntimeError(f"FFmpeg compression failed: {compress_stderr.decode()}")
            
            # Check compressed file size
            if not os.path.exists(temp_output_path):
                raise RuntimeError("Temp output file not created")
            
            output_size = os.path.getsize(temp_output_path)
            logger.info(f"Compressed size: {output_size / 1024 / 1024:.2f}MB")
            
            if output_size <= target_size_bytes:
                # Success - replace original file
                logger.info("Target size achieved!")
                os.replace(temp_output_path, input_path)
                logger.info(f"Video compressed successfully: {input_path}")
                return
            
            # Too big, use this compressed file as input for next attempt
            ratio = target_size_bytes / output_size
            current_bitrate_factor *= ratio * 0.9  # 10% safety margin
            current_input_path = temp_output_path
            logger.info(f"File too big, reducing bitrate factor to {current_bitrate_factor:.3f}, using compressed file as next input")
        
        # All attempts failed
        raise RuntimeError(f"Failed to compress video after {max_attempts} attempts")
    
    finally:
        # Clean up all temp files
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {temp_file}: {e}")