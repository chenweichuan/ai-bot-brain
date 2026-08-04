# encoding:utf-8
import copy
import json
import httpx

from common.log import logger
from common.message import truncate_media_urls_for_logging
from config import conf
from providers.llm.converter import (
    to_responses_request,
    to_completions_response,
    to_completions_chunk,
)
from providers.llm.quirks import preprocess_request


"""Load model_providers config as a name-indexed dict."""
MODEL_PROVIDERS = { p["name"]: p for p in conf().get("model_providers", []) }

"""Load model_routes as a flat list of (prefix, provider, format) entries,
sorted by longest prefix first so more specific prefixes always win."""
MODEL_ROUTES = []
for route in conf().get("model_routes", []):
    provider = route["provider"]
    format = route.get("format", "completions")
    for prefix in route.get("prefixes", []):
        MODEL_ROUTES.append((prefix, provider, format))
MODEL_ROUTES.sort(key=lambda e: len(e[0]), reverse=True)

def _resolve_route(model: str):
    """Resolve provider connection config and API format for a model name."""
    for prefix, provider_name, format in MODEL_ROUTES:
        if model.startswith(prefix):
            if provider_name not in MODEL_PROVIDERS:
                raise ValueError(
                    f"Provider '{provider_name}' not found in model_providers config"
                )
            return MODEL_PROVIDERS[provider_name], format
    raise ValueError(f"No route configured for model: {model}")


class LlmClient:
    """
    Unified LLM client.

    Resolved by model prefix from config. Each instance holds the provider
    connection info (api_base / api_key) and the API format (completions or
    responses) for the matched model prefix.
    """

    _instances = {}

    def __init__(self, provider: dict, model_format: str):
        self.provider_name = provider["name"]
        self.api_base = provider["api_base"]
        self.api_key = provider["api_key"]
        self.model_format = model_format

    @classmethod
    def factory(cls, model: str) -> "LlmClient":
        """
        Create or return a cached LlmClient for the given model.

        Resolution is purely prefix-based against model_routes config:
        1. Find the first route whose prefix matches the model name
           (longest prefix wins).
        2. Look up the corresponding provider connection config.
        3. Return a client configured with endpoint + format.
        """
        provider, format = _resolve_route(model)
        cache_key = (provider["name"], format)
        if cache_key not in cls._instances:
            cls._instances[cache_key] = cls(provider, format)
        return cls._instances[cache_key]

    async def chat(self, **request):
        request = copy.deepcopy(request)
        request_format = "responses" if "input" in request else "completions"

        # Preprocess requests before any conversion:
        # custom media URL -> base64, completions structured content -> plain text, provider quirks.
        await preprocess_request(request)

        # Convert request to the model's native format when needed.
        if request_format != self.model_format and self.model_format == "responses":
            request = to_responses_request(request)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        endpoint = "responses" if self.model_format == "responses" else "chat/completions"
        url = f"{self.api_base}/{endpoint}"

        log_request = truncate_media_urls_for_logging(request)
        logger.info(f"[{self.provider_name}] LLM request: {json.dumps(log_request, ensure_ascii=False)}")

        if request.get("stream"):
            async def process_stream():
                ctx = None
                async with httpx.AsyncClient() as client:
                    try:
                        async with client.stream(
                            "POST", url, headers=headers, json=request, timeout=600.0
                        ) as response:
                            response.raise_for_status()

                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue

                                data = line[5:].strip()
                                if data == "[DONE]":
                                    break

                                try:
                                    chunk = json.loads(data)

                                    logger.info(
                                        f"[{self.provider_name}] LLM response chunk: "
                                        f"{json.dumps(chunk, ensure_ascii=False)}"
                                    )
                                    if chunk.get("usage"):
                                        logger.info(
                                            f"[{self.provider_name}] Token usage "
                                            f"({chunk.get('model', request.get('model'))}): "
                                            f"{json.dumps(chunk['usage'], ensure_ascii=False)}"
                                        )

                                    if request_format != self.model_format and request_format == "completions":
                                        chunk, ctx = to_completions_chunk(chunk, ctx)
                                        if not chunk:
                                            continue

                                    yield chunk
                                except json.JSONDecodeError:
                                    continue
                    except Exception as e:
                        logger.error(f"{self.provider_name} stream error: {e}")
                        raise

            return process_stream()
        else:
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(url, headers=headers, json=request, timeout=600.0)
                    response.raise_for_status()
                    result = response.json()

                    logger.info(f"[{self.provider_name}] LLM response: {json.dumps(result, ensure_ascii=False)}")
                    if result.get("usage"):
                        logger.info(
                            f"[{self.provider_name}] Token usage ({result.get('model', request.get('model'))}): "
                            f"{json.dumps(result['usage'], ensure_ascii=False)}"
                        )

                    if request_format != self.model_format and request_format == "completions":
                        result = to_completions_response(result)

                    return result
                except Exception as e:
                    logger.error(f"{self.provider_name} request error: {e}")
                    raise