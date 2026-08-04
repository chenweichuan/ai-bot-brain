# encoding:utf-8
"""
Pure format converters between Chat Completions and Responses API formats.

These functions are stateless and hold no client reference. Preprocessing
(e.g. media URL -> base64, structured content -> plain text) is handled by
the caller before invoking these converters.

Currently implements Completions -> Responses conversion (the direction used
by Volcengine Ark doubao-seed-2 models). Additional conversion directions
can be added here as needed.
"""


def to_responses_request(request: dict) -> dict:
    """Convert a Chat Completions request to Responses API request format."""
    new_input = []
    for msg in request["messages"]:
        role = msg["role"]

        if role == "assistant":
            # reasoning_content splits into a standalone reasoning item
            if msg.get("reasoning_content"):
                new_input.append(
                    {
                        "type": "reasoning",
                        "summary": [
                            {"type": "summary_text", "text": msg["reasoning_content"]}
                        ],
                    }
                )
            # content passes through
            if msg.get("content"):
                new_input.append({"role": "assistant", "content": msg["content"]})
            # tool_calls split into function_call items
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    new_input.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": tc["function"].get("name", ""),
                            "arguments": tc["function"].get("arguments", ""),
                        }
                    )

        elif role == "tool":
            # tool role -> function_call_output
            new_input.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content"),
                }
            )

        elif role == "user" and isinstance(msg.get("content"), list):
            # User structured content needs field name conversion
            new_content = []
            for part in msg.get("content"):
                pt = part.get("type", "")
                if pt == "image_url":
                    img = part.get("image_url", {})
                    url = img.get("url", "") if isinstance(img, dict) else img
                    new_content.append({"type": "input_image", "image_url": url})
                elif pt == "video_url":
                    vid = part.get("video_url", {})
                    url = vid.get("url", "") if isinstance(vid, dict) else vid
                    item = {"type": "input_video", "video_url": url}
                    if isinstance(vid, dict) and "fps" in vid:
                        item["fps"] = vid["fps"]
                    new_content.append(item)
                elif pt == "text":
                    new_content.append({"type": "input_text", "text": part.get("text", "")})
                else:
                    new_content.append(part)
            new_input.append({"role": "user", "content": new_content})
        else:
            # Default: keep original message as-is
            new_input.append(msg)

    # The last assistant message must be marked partial
    if new_input[-1].get("role") == "assistant":
        new_input[-1]["partial"] = True

    new_request = {
        "model": request["model"],
        "input": new_input,
    }

    for key in [
        "stream",
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
    ]:
        if request.get(key) is not None:
            new_request[key] = request.get(key)

    # max_tokens (Completions) -> max_output_tokens (Responses)
    if request.get("max_tokens") is not None:
        new_request["max_output_tokens"] = request["max_tokens"]

    # thinking / reasoning
    # Upstream sends thinking=True/False (boolean), convert to Responses thinking.type
    thinking = request.get("thinking")
    if thinking is not None:
        if isinstance(thinking, bool):
            new_request["thinking"] = {"type": "enabled" if thinking else "disabled"}
            # When thinking is disabled, reasoning.effort must be minimal
            if not thinking:
                new_request["reasoning"] = {"effort": "minimal"}
        elif isinstance(thinking, dict):
            # Support direct thinking={"type": "enabled"/"disabled"/"auto"}
            new_request["thinking"] = thinking
            if thinking.get("type") == "disabled":
                new_request["reasoning"] = {"effort": "minimal"}

    # reasoning.effort independently controls thinking length
    if request.get("reasoning_effort") is not None:
        effort = request["reasoning_effort"]
        new_request["reasoning"] = {"effort": effort}
        # minimal is equivalent to disabling thinking
        if effort == "minimal":
            new_request["thinking"] = {"type": "disabled"}

    # tools
    if request.get("tools"):
        new_request["tools"] = []
        for tool in request["tools"]:
            if tool.get("type") == "function":
                new_request["tools"].append(
                    {
                        "type": "function",
                        "name": tool["function"].get("name", ""),
                        "description": tool["function"].get("description", ""),
                        "parameters": tool["function"].get("parameters", {}),
                    }
                )
            else:
                # Other tool types pass through directly
                new_request["tools"].append(tool)

    # tool_choice
    if request.get("tool_choice") is not None:
        tool_choice = request["tool_choice"]
        if isinstance(tool_choice, str):
            new_request["tool_choice"] = tool_choice
        elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            new_request["tool_choice"] = {
                "type": "function",
                "name": tool_choice["function"].get("name", ""),
            }

    # response_format
    if request.get("response_format"):
        new_request["text"] = {"format": request["response_format"]}

    return new_request


def to_completions_response(response: dict) -> dict:
    """Convert a non-stream Responses API response to Chat Completions format."""
    output_items = response.get("output", [])

    reasoning_content_parts = []
    content_parts = []
    tool_calls = []

    for item in output_items:
        item_type = item.get("type", "")
        if item_type == "reasoning":
            summary = item.get("summary", [])
            for part in summary:
                if part.get("type") == "summary_text":
                    reasoning_content_parts.append(part.get("text", ""))
        elif item_type == "message":
            content = item.get("content", [])
            for part in content:
                if part.get("type") == "output_text":
                    content_parts.append(part.get("text", ""))
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                }
            )

    message = { "role": "assistant" }
    if reasoning_content_parts:
        message["reasoning_content"] = "\n\n".join(reasoning_content_parts)
    if content_parts:
        message["content"] = "\n\n".join(content_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls

    status = response.get("status")
    if tool_calls:
        finish_reason = "tool_calls"
    elif status == "incomplete":
        finish_reason = response.get("incomplete_details", {}).get("reason", "stop")
    else:
        finish_reason = "stop"

    usage = to_completions_usage(response.get("usage", {}))

    return {
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "created": response.get("created_at"),
        "id": response.get("id", ""),
        "model": response.get("model"),
        "service_tier": response.get("service_tier", ""),
        "object": "chat.completion",
        "usage": usage,
    }


def to_completions_chunk(chunk: dict, context: dict = None):
    """Convert a single Responses SSE chunk to a Chat Completions chunk.

    Returns (new_chunk, updated_context). new_chunk may be None if the
    event has no delta to emit (e.g. response.created, first reasoning part).
    """
    context = context or {
        "common_fields": {
            "created": None,
            "id": None,
            "model": None,
            "service_tier": None,
        },
        "has_reasoning_content": False,
        "has_content": False,
        "tool_call_item_ids": [],
    }
    common_fields = context["common_fields"]
    tool_call_item_ids = context["tool_call_item_ids"]

    chunk_type = chunk.get("type", "")
    new_chunk = None

    if chunk_type == "response.created":
        result = chunk.get("response", {})
        common_fields["created"] = result.get("created_at")
        common_fields["id"] = result.get("id")
        common_fields["model"] = result.get("model")
        common_fields["service_tier"] = result.get("service_tier")
    elif (
        chunk_type == "response.reasoning_summary_part.added"
        and chunk.get("part", {}).get("type") == "summary_text"
    ):
        if not context["has_reasoning_content"]:
            context["has_reasoning_content"] = True
        else:
            new_chunk = create_completions_chunk(
                reasoning_content="\n\n", **common_fields
            )

    elif chunk_type == "response.reasoning_summary_text.delta":
        delta_reasoning = chunk.get("delta", "")
        if delta_reasoning:
            new_chunk = create_completions_chunk(
                reasoning_content=delta_reasoning, **common_fields
            )

    elif (
        chunk_type == "response.content_part.added"
        and chunk.get("part", {}).get("type") == "output_text"
    ):
        if not context["has_content"]:
            context["has_content"] = True
        else:
            new_chunk = create_completions_chunk(content="\n\n", **common_fields)

    elif chunk_type == "response.output_text.delta":
        delta_text = chunk.get("delta", "")
        if delta_text:
            new_chunk = create_completions_chunk(content=delta_text, **common_fields)

    elif (
        chunk_type == "response.output_item.added"
        and chunk.get("item", {}).get("type") == "function_call"
    ):
        item = chunk.get("item", {})
        tool_call_item_ids.append(item.get("id", ""))
        tool_call = {
            "function": {
                "arguments": "",
                "name": item.get("name", ""),
            },
            "id": item.get("call_id", ""),
            "index": len(tool_call_item_ids) - 1,
            "type": "function",
        }
        new_chunk = create_completions_chunk(tool_call=tool_call, **common_fields)

    elif chunk_type == "response.function_call_arguments.delta":
        delta_args = chunk.get("delta", "")
        item_id = chunk.get("item_id", "")
        try:
            tc_index = tool_call_item_ids.index(item_id)
        except ValueError:
            pass
        else:
            if delta_args:
                tool_call = {"function": {"arguments": delta_args}, "index": tc_index}
                new_chunk = create_completions_chunk(tool_call=tool_call, **common_fields)

    elif chunk_type == "response.completed":
        result = chunk.get("response", {})
        usage = to_completions_usage(result.get("usage"))
        finish_reason = "tool_calls" if tool_call_item_ids else "stop"
        new_chunk = create_completions_chunk(
            finish_reason=finish_reason, usage=usage, **common_fields
        )

    elif chunk_type == "response.incomplete":
        result = chunk.get("response", {})
        usage = to_completions_usage(result.get("usage"))
        finish_reason = result.get("incomplete_details", {}).get(
            "reason", "stop"
        )
        new_chunk = create_completions_chunk(
            finish_reason=finish_reason, usage=usage, **common_fields
        )

    return new_chunk, context

def create_completions_chunk(
    reasoning_content: str = None,
    content: str = None,
    tool_call: dict = None,
    finish_reason: str = None,
    created: str = None,
    id: str = None,
    model: str = None,
    service_tier: str = None,
    usage: dict = None,
) -> dict:
    """Build a Chat Completions format chunk from Responses delta data."""
    delta = { "role": "assistant" }

    if reasoning_content:
        delta["reasoning_content"] = reasoning_content
    if content:
        delta["content"] = content
    if tool_call:
        delta["tool_calls"] = [tool_call]

    result = {
        "choices": [
            {
                "delta": delta,
                "index": 0,
            }
        ],
        "created": created,
        "id": id,
        "model": model,
        "service_tier": service_tier,
        "object": "chat.completion.chunk",
        "usage": usage,
    }

    if finish_reason:
        result["choices"][0]["finish_reason"] = finish_reason

    return result


def to_completions_usage(usage: dict) -> dict:
    """Convert Responses usage to Chat Completions format."""
    if not usage:
        return None
    return {
        "completion_tokens": usage.get("output_tokens", 0),
        "prompt_tokens": usage.get("input_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "prompt_tokens_details": usage.get("input_tokens_details", {}),
        "completion_tokens_details": usage.get("output_tokens_details", {}),
    }
