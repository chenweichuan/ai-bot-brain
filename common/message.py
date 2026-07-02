from datetime import datetime
import copy
import json
import math


def truncate_media_urls_for_logging(request: dict) -> dict:
    """
    Create a copy of the request with media URLs truncated to 500 characters for logging.
    Truncates image_url and video_url fields in message content.
    """
    log_request = copy.deepcopy(request)
    
    if "messages" in log_request:
        for msg in log_request["messages"]:
            if isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    # Truncate image_url urls
                    if part.get("type") == "image_url" and "image_url" in part:
                        url = part["image_url"].get("url", "")
                        if len(url) > 500:
                            part["image_url"]["url"] = url[:500] + "...[truncated]"
                    # Truncate video_url urls
                    if part.get("type") == "video_url" and "video_url" in part:
                        url = part["video_url"].get("url", "")
                        if len(url) > 500:
                            part["video_url"]["url"] = url[:500] + "...[truncated]"
                    # Also handle the original image/video format (before conversion)
                    if part.get("type") == "image" and "image" in part:
                        url = part["image"].get("url", "")
                        if len(url) > 500:
                            part["image"]["url"] = url[:500] + "...[truncated]"
                    if part.get("type") == "video" and "video" in part:
                        url = part["video"].get("url", "")
                        if len(url) > 500:
                            part["video"]["url"] = url[:500] + "...[truncated]"
    
    return log_request


def count_text_units(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 3)

def count_messages_text_units(messages: list[dict]) -> int:
    total_length = 0
    for cur in messages:
        total_length += count_text_units(" ".join(list(filter(lambda s: s, [
            cur.get("role"),
            cur.get("reasoning_content"),
            json.dumps(cur["content"], ensure_ascii=False) if cur.get("content") else "",
            cur.get("name"),
            json.dumps(cur["tool_calls"], ensure_ascii=False) if cur.get("tool_calls") else "",
        ]))))
    return total_length

def stringify_message_content(content: str | list[dict]) -> str:
    if isinstance(content, list):
        text_segments: list[str] = []
        for part in content:
            try:
                part_type = part.get("type")
                if part_type == "text":
                    text_segments.append(part.get("text") or "")
                elif part_type == "image":
                    url = (part.get("image") or {}).get("url")
                    text_segments.append(f"![]({url})" if url.startswith("http") else f"image path: {url}")
                elif part_type == "audio":
                    url = (part.get("audio") or {}).get("url")
                    text_segments.append(f"!audio[]({url})" if url.startswith("http") else f"audio path: {url}")
                elif part_type == "video":
                    url = (part.get("video") or {}).get("url")
                    text_segments.append(f"!video[]({url})" if url.startswith("http") else f"video path: {url}")
                elif part_type == "document":
                    url = (part.get("document") or {}).get("url")
                    filename = (part.get("document") or {}).get("filename")
                    text_segments.append(f"[{filename}]({url})" if url.startswith("http") else f"document ({filename}) path: {url}")
                elif part_type == "attachment":
                    url = (part.get("attachment") or {}).get("url")
                    filename = (part.get("attachment") or {}).get("filename")
                    text_segments.append(f"[{filename}]({url})" if url.startswith("http") else f"attachment ({filename}) path: {url}")
                else:
                    text_segments.append(json.dumps(part, ensure_ascii=False))
            except Exception:
                text_segments.append(json.dumps(part, ensure_ascii=False))
        return "\n\n".join(text_segments)
    else:
        return content

def stringify_message(msg: dict[str, any]) -> str:
    msg_str = ""
    
    # Stringify message content and tool calls
    content = stringify_message_content(msg["content"] if msg["role"] != "tool" else msg.get("summary") or "")
    if msg.get("tool_calls"):
        tool_calls = list(map(lambda call:
            f"Call tool {call['function']['name']} with arguments: {call['function']['arguments']}"
        , msg["tool_calls"]))
        content += "\n\n" + "\n".join(tool_calls)

    # Truncate content if it exceeds the maximum number of text units
    max_content_units = 5000
    if count_text_units(content) > max_content_units:
        truncated_length = len(content) / count_text_units(content) * max_content_units * 0.9
        content = f"{content[:int(truncated_length/2)]}\n...[Content Truncated]...\n{content[-int(truncated_length/2):]}"
        
    if msg.get("role") == "system":
        msg_str += f"System:\n\n{content}"
    elif msg.get("role") == "user":
        msg_str += "User "
        msg_str += f"(named {msg['name']}) " if msg.get("name") else ""
        msg_str += f"at {datetime.fromtimestamp(msg['timestamp'] // 1_000).strftime('%Y-%m-%d %H:%M:%S')} " \
            f"says:\n\n{content}"
    elif msg.get("role") == "assistant":
        msg_str += "Assistant " \
            f"at {datetime.fromtimestamp(msg['timestamp'] // 1_000).strftime('%Y-%m-%d %H:%M:%S')} " \
            "replies"
        msg_str += f" to user (named {msg['to_name']})" if msg.get("to_name") else ""
        msg_str += f":\n\n{content}"
    elif msg.get("role") == "tool":
        msg_str += f"Tool "
        msg_str += f"{msg['name']} " if msg.get("name") else ""
        msg_str += f"at {datetime.fromtimestamp(msg['timestamp'] // 1_000).strftime('%Y-%m-%d %H:%M:%S')} " \
            f"executes:\n\n{content}"

    return msg_str
