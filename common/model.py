
"""Load model_providers config as a name-indexed dict."""
from config import conf


MODEL_PROVIDERS = { p["name"]: p for p in conf().get("model_providers", []) }

"""Load model_routes as a flat list of (prefix, route_dict) entries,
sorted by longest prefix first so more specific prefixes always win."""
MODEL_ROUTES = []
for route in conf().get("model_routes", []):
    provider = MODEL_PROVIDERS[route["provider"]]
    for prefix in route.get("prefixes", []):
        MODEL_ROUTES.append((prefix, provider, route))
MODEL_ROUTES.sort(key=lambda e: len(e[0]), reverse=True)

def resolve_route(model: str):
    """Resolve route config for a model name.

    Returns a dict with keys:
      - provider: provider connection dict (name, api_base, api_key, ...)
      - format: API format ("completions" or "responses")
      - vision_fallback: vision model fallback mapping from provider config
    """
    for prefix, provider, route in MODEL_ROUTES:
        if model.startswith(prefix):
            return provider, route
    raise ValueError(f"No route configured for model: {model}")
