"""The catalogue of models OpenRouter can route to.

OpenRouter fronts several hundred models behind one API key, and the set
changes weekly. Typing the id by hand means a silent 404 at translation time
for every typo, so the list is fetched from the service and cached on disk;
the cache is what makes the picker usable offline and on first paint.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from modules.utils.download_file import open_url
from modules.utils.paths import get_user_data_dir

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_FILENAME = "openrouter_models.json"

# The catalogue moves slowly enough that a day-old copy is still useful, and
# refreshing on every settings open would spend a request on nothing.
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class OpenRouterModel:
    id: str
    name: str
    context_length: int
    prompt_price: float
    completion_price: float
    supports_images: bool

    @property
    def is_free(self) -> bool:
        return self.prompt_price == 0.0 and self.completion_price == 0.0

    def describe(self) -> str:
        """The secondary line under the model id: how big, and what it costs."""
        parts = []
        if self.context_length:
            parts.append(f"{self.context_length // 1000}k ctx")
        if self.supports_images:
            parts.append("vision")
        if self.is_free:
            parts.append("free")
        else:
            # Prices arrive per token; per-million is the unit people compare in.
            parts.append(f"${self.prompt_price * 1e6:.2f} in / ${self.completion_price * 1e6:.2f} out per M")
        return "  ·  ".join(parts)


def _cache_path() -> str:
    return os.path.join(get_user_data_dir(), CACHE_FILENAME)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_model(entry: dict[str, Any]) -> OpenRouterModel | None:
    model_id = entry.get("id")
    if not model_id:
        return None

    pricing = entry.get("pricing") or {}
    architecture = entry.get("architecture") or {}
    modalities = architecture.get("input_modalities") or []

    return OpenRouterModel(
        id=str(model_id),
        name=str(entry.get("name") or model_id),
        context_length=int(entry.get("context_length") or 0),
        prompt_price=_to_float(pricing.get("prompt")),
        completion_price=_to_float(pricing.get("completion")),
        supports_images="image" in modalities,
    )


def parse_models(payload: dict[str, Any]) -> list[OpenRouterModel]:
    entries = payload.get("data")
    if not isinstance(entries, list):
        return []
    models = [_parse_model(entry) for entry in entries if isinstance(entry, dict)]
    # Alphabetical, which groups each provider's models together.
    return sorted([m for m in models if m is not None], key=lambda m: m.id)


def load_cached_models() -> list[OpenRouterModel]:
    """Models from the last successful fetch, or an empty list."""
    try:
        with open(_cache_path(), "r", encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError):
        return []
    return parse_models(cached.get("payload", {}))


def cache_age_seconds() -> float | None:
    """Seconds since the cache was written, or None when there is no cache."""
    try:
        return time.time() - os.path.getmtime(_cache_path())
    except OSError:
        return None


def cache_is_fresh() -> bool:
    age = cache_age_seconds()
    return age is not None and age < CACHE_MAX_AGE_SECONDS


def _write_cache(payload: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": time.time(), "payload": payload}, handle)
    except OSError:
        pass  # A missing cache costs a refresh, not a failure.


def fetch_models(api_key: str = "", timeout: float = 15.0) -> list[OpenRouterModel]:
    """Fetch the live catalogue and cache it.

    The endpoint is public; the key is sent when present only so the account's
    own model access is reflected.

    :raises OSError: on any network or parse failure.
    """
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with open_url(MODELS_URL, headers=headers, timeout=timeout) as response:
        raw = response.read()

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OSError(f"OpenRouter returned a response that is not JSON: {exc}") from exc

    models = parse_models(payload)
    if models:
        _write_cache(payload)
    return models


def get_models(api_key: str = "", force_refresh: bool = False) -> list[OpenRouterModel]:
    """Cached catalogue, refreshed when stale or asked for.

    Falls back to whatever is cached when the fetch fails, so losing the network
    degrades the picker to a slightly stale list rather than an empty one.
    """
    if not force_refresh and cache_is_fresh():
        cached = load_cached_models()
        if cached:
            return cached

    try:
        return fetch_models(api_key)
    except Exception:
        return load_cached_models()
