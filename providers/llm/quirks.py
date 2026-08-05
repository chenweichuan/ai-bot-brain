from common.log import logger
from common.message import stringify_message_content
from providers.llm.media import get_base64_data_url


async def preprocess_request(request: dict, vision_fallback: dict = None):
    """Preprocessing shared by both Completions and Responses request paths."""
    # Vision model fallback
    has_visual_content = any(
        isinstance(msg.get("content"), list)
        and any(
            (part or {}).get("type") in ["image_url", "video_url"]
            for part in msg.get("content")
        )
        for msg in request.get("messages", [])
    ) or any(
        isinstance(item.get("content"), list)
        and any(
            (part or {}).get("type") in ["input_image", "input_video"]
            for part in item.get("content")
        )
        for item in request.get("input", [])
    )
    if has_visual_content:
        vision_model = vision_fallback.get(request.get("model")) if isinstance(vision_fallback, dict) else vision_fallback
        if vision_model:
            request["model"] = vision_model

    for msg in request.get("messages", []):
        # Non-user roles do not support structured content -> plain text
        if msg["role"] != "user":
            msg["content"] = stringify_message_content(msg.get("content"))
        # Convert custom image/video part types for user structured messages
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for part in msg["content"]:
                if part["type"] == "image":
                    try:
                        if not part["image"]["url"].startswith("data:"):
                            part["image"]["url"] = await get_base64_data_url(
                                part["image"]["url"], "image"
                            )
                        part["type"] = "image_url"
                        part["image_url"] = part["image"]
                        del part["image"]
                    except Exception as e:
                        logger.error(
                            f"Failed to process image: {part['image']['url']}, error: {e}"
                        )
                        part["type"] = "text"
                        part["text"] = f"Image Unavailable: {part['image']['url']}"
                        del part["image"]
                elif part["type"] == "video":
                    try:
                        if not part["video"]["url"].startswith("data:"):
                            part["video"]["url"] = await get_base64_data_url(
                                part["video"]["url"], "video"
                            )
                        part["type"] = "video_url"
                        part["video_url"] = part["video"]
                        del part["video"]
                    except Exception as e:
                        logger.error(
                            f"Failed to process video: {part['video']['url']}, error: {e}"
                        )
                        part["type"] = "text"
                        part["text"] = f"Video Unavailable: {part['video']['url']}"
                        del part["video"]

    # thinking bool -> thinking object
    if request.get("thinking") is True:
        request["thinking"] = {"type": "enabled"}
    elif request.get("thinking") is False:
        request["thinking"] = {"type": "disabled"}

    if request["model"].startswith("glm-"):
        preprocess_glm_request(request)
    elif request["model"].startswith("gpt-"):
        preprocess_gpt_request(request)

def preprocess_gpt_request(request: dict):
    """OpenAI-specific parameter adjustments."""
    is_completions_format = "messages" in request
    
    for msg in request.get("messages", []):
        if msg["role"] == "system":
            msg["role"] = "developer"
        if "reasoning_content" in msg:
            del msg["reasoning_content"]

    # thinking dict -> reasoning dict
    if request.get("thinking") is not None:
        is_disabled = request["thinking"].get("type") == "disabled"
        if is_disabled and is_completions_format:
            request["reasoning_effort"] = "none"
        elif is_disabled:
            request["reasoning"] = { "effort": "none" }
        del request["thinking"]

def preprocess_glm_request(request: dict):
    """ZhipuAI-specific parameter adjustments."""
    # Stream tool calls as well
    request["tool_stream"] = request.get("stream")
