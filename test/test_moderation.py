#!/usr/bin/env python3
# encoding:utf-8
"""
内容审核模块测试脚本
测试图片、视频、音频、文档四种类型的文件审核
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import asyncio
from common.content_moderation.client import ContentModerationClient
content_moderation = ContentModerationClient.get_instance()


def resolve_short_url(url: str) -> str:
    """解析短链接，获取真实URL"""
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        return response.url
    except Exception as e:
        print(f"解析短链接失败: {e}")
        return url


async def main():
    print("=" * 80)
    print("内容审核模块测试")
    print("=" * 80)
    print(f"审核模块启用状态: {content_moderation.enabled}")
    print()

    test_files = [
        ("图片", "https://tzone.ink/l/VMM"),
        ("视频", "https://tzone.ink/l/Uvc"),
        ("语音", "https://tzone.ink/l/RFw"),
        ("文档", "https://tzone.ink/l/VMU"),
    ]

    for file_type, url in test_files:
        print("-" * 80)
        print(f"测试 {file_type} 审核: {url}")

        real_url = resolve_short_url(url)
        print(f"真实URL: {real_url}")

        if file_type == "图片":
            result = await content_moderation.check_image(real_url)
        elif file_type == "视频":
            result = await content_moderation.check_video(real_url)
        elif file_type == "语音":
            result = await content_moderation.check_audio(real_url)
        elif file_type == "文档":
            result = await content_moderation.check_document(real_url)
        else:
            print(f"未知类型: {file_type}")
            continue

        print(f"审核状态: {result.status}")
        print(f"审核消息: {result.message}")
        if result.details:
            print(f"详情: {result.details}")
        print("-" * 80)
        print()


if __name__ == "__main__":
    asyncio.run(main())