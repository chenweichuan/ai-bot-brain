#!/usr/bin/env python3
"""
Test script to verify the truncate_media_urls_for_logging function works correctly.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.message import truncate_media_urls_for_logging
import json


def test_truncate_image_url():
    """Test that image_url urls are truncated"""
    long_url = "https://example.com/image.jpg?" + "x" * 600
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": long_url
                        }
                    }
                ]
            }
        ]
    }
    
    log_request = truncate_media_urls_for_logging(request)
    truncated_url = log_request["messages"][0]["content"][0]["image_url"]["url"]
    
    assert truncated_url.endswith("...[truncated]"), "URL should end with [truncated]"
    assert len(truncated_url) < len(long_url), "Truncated URL should be shorter than original"
    print("✓ Image URL truncation test passed")


def test_truncate_video_url():
    """Test that video_url urls are truncated"""
    long_url = "https://example.com/video.mp4?" + "x" * 600
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": long_url
                        }
                    }
                ]
            }
        ]
    }
    
    log_request = truncate_media_urls_for_logging(request)
    truncated_url = log_request["messages"][0]["content"][0]["video_url"]["url"]
    
    assert truncated_url.endswith("...[truncated]"), "URL should end with [truncated]"
    assert len(truncated_url) < len(long_url), "Truncated URL should be shorter than original"
    print("✓ Video URL truncation test passed")


def test_original_image_format():
    """Test that original image format urls are truncated"""
    long_url = "https://example.com/image.jpg?" + "x" * 600
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "image": {
                            "url": long_url
                        }
                    }
                ]
            }
        ]
    }
    
    log_request = truncate_media_urls_for_logging(request)
    truncated_url = log_request["messages"][0]["content"][0]["image"]["url"]
    
    assert truncated_url.endswith("...[truncated]"), "URL should end with [truncated]"
    assert len(truncated_url) < len(long_url), "Truncated URL should be shorter than original"
    print("✓ Original image format test passed")


def test_original_video_format():
    """Test that original video format urls are truncated"""
    long_url = "https://example.com/video.mp4?" + "x" * 600
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video",
                        "video": {
                            "url": long_url
                        }
                    }
                ]
            }
        ]
    }
    
    log_request = truncate_media_urls_for_logging(request)
    truncated_url = log_request["messages"][0]["content"][0]["video"]["url"]
    
    assert truncated_url.endswith("...[truncated]"), "URL should end with [truncated]"
    assert len(truncated_url) < len(long_url), "Truncated URL should be shorter than original"
    print("✓ Original video format test passed")


def test_short_urls_not_truncated():
    """Test that short urls are not truncated"""
    short_url = "https://example.com/short.jpg"
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": short_url
                        }
                    }
                ]
            }
        ]
    }
    
    log_request = truncate_media_urls_for_logging(request)
    result_url = log_request["messages"][0]["content"][0]["image_url"]["url"]
    
    assert result_url == short_url, "Short URL should not be truncated"
    print("✓ Short URL not truncated test passed")


def test_request_unchanged():
    """Test that the original request is not modified"""
    long_url = "https://example.com/image.jpg?" + "x" * 600
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": long_url
                        }
                    }
                ]
            }
        ]
    }
    
    original_request = json.dumps(request, ensure_ascii=False)
    truncate_media_urls_for_logging(request)
    current_request = json.dumps(request, ensure_ascii=False)
    
    assert original_request == current_request, "Original request should not be modified"
    print("✓ Original request unchanged test passed")


def test_multiple_messages_and_parts():
    """Test multiple messages and parts are handled correctly"""
    long_url1 = "https://example.com/1.jpg?" + "x" * 600
    long_url2 = "https://example.com/2.mp4?" + "x" * 600
    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {
                        "type": "image_url",
                        "image_url": {"url": long_url1}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {"url": long_url2}
                    }
                ]
            }
        ]
    }
    
    log_request = truncate_media_urls_for_logging(request)
    
    url1 = log_request["messages"][0]["content"][1]["image_url"]["url"]
    url2 = log_request["messages"][1]["content"][0]["video_url"]["url"]
    
    assert url1.endswith("...[truncated]") and len(url1) < len(long_url1)
    assert url2.endswith("...[truncated]") and len(url2) < len(long_url2)
    print("✓ Multiple messages and parts test passed")


def main():
    print("Testing truncate_media_urls_for_logging function...")
    print("=" * 60)
    
    try:
        test_truncate_image_url()
        test_truncate_video_url()
        test_original_image_format()
        test_original_video_format()
        test_short_urls_not_truncated()
        test_request_unchanged()
        test_multiple_messages_and_parts()
        
        print("=" * 60)
        print("✅ All tests passed!")
        return 0
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())