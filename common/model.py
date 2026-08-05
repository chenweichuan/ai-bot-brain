
"""Model route resolution: maps model name prefixes to provider configs."""

from config import conf


# provider name -> provider config dict, indexed by name for fast lookup
MODEL_PROVIDERS = { p["name"]: p for p in conf().get("model_providers", []) }

# Flat list of (prefix, provider_dict, route_dict) tuples,
# sorted by longest prefix first so more specific prefixes always match first.
MODEL_ROUTES = []
for route in conf().get("model_routes", []):
    provider = MODEL_PROVIDERS[route["provider"]]
    for prefix in route.get("prefixes", []):
        MODEL_ROUTES.append((prefix, provider, route))
MODEL_ROUTES.sort(key=lambda e: len(e[0]), reverse=True)


def resolve_route(model: str):
    """Resolve provider and route config for a given model name.

    Iterates through MODEL_ROUTES (sorted by longest prefix first) and
    returns the first matching route. Matching is done by prefix comparison
    on the model name.

    Args:
        model: The model name to resolve (e.g. "doubao-seed-2.0-lite").

    Returns:
        A tuple of (provider, route):
          - provider: provider connection dict with keys like name, api_base,
            api_key, format, vision_fallback, etc.
          - route: route config dict from model_routes with keys like
            provider, prefixes, and any route-specific overrides.

    Raises:
        ValueError: If no matching route is found for the model name.
    """
    for prefix, provider, route in MODEL_ROUTES:
        if model.startswith(prefix):
            return provider, route
    raise ValueError(f"No route configured for model: {model}")
