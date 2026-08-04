from common.log import logger
from common.message import stringify_message_content
from config import conf
from providers.llm.media import get_base64_data_url


MODEL_VISION_FALLBACK = conf().get("model_vision_fallback", {})

async def preprocess_request(request: dict):
    """Preprocessing shared by both Completions and Responses request paths."""
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

    if request["model"].startswith("gpt-"):
        preprocess_gpt_request(request)
    elif request["model"].startswith("glm-"):
        preprocess_glm_request(request)

    # Vision model fallback
    has_visual_content = any(
        isinstance(msg.get("content"), list)
        and any(
            (part or {}).get("type") in ["image_url", "video_url"]
            for part in msg.get("content", [])
        )
        for msg in request.get("messages", [])
    )
    if has_visual_content:
        vision_model = MODEL_VISION_FALLBACK.get(request.get("model"))
        if vision_model:
            request["model"] = vision_model

def preprocess_gpt_request(request: dict):
    """OpenAI-specific parameter adjustments."""
    for msg in request.get("messages", []):
        if msg["role"] == "system":
            msg["role"] = "developer"
        if "reasoning_content" in msg:
            del msg["reasoning_content"]

    # thinking bool -> reasoning_effort string
    if request.get("thinking") is not None:
        thinking = request["thinking"]
        if isinstance(thinking, bool):
            request["reasoning_effort"] = "high" if thinking else "low"
        del request["thinking"]

    # top_p is not supported by OpenAI reasoning models
    if "top_p" in request:
        del request["top_p"]

def preprocess_glm_request(request: dict):
    """ZhipuAI-specific parameter adjustments."""
    # Default: disable built-in web search
    request["tools"] = request.get("tools") or []
    if not any(tool.get("type") == "web_search" for tool in request["tools"]):
        request["tools"].append({
            "type": "web_search",
            "web_search": {"enable": False},
        })

    # Stream tool calls as well
    request["tool_stream"] = request.get("stream")

    # thinking bool -> ZhipuAI thinking object
    if request.get("thinking") is True:
        request["thinking"] = {"type": "enabled"}
    elif request.get("thinking") is False:
        request["thinking"] = {"type": "disabled"}
