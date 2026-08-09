"""Persistent file-based cache for Telegram search results."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from shelfmark.config import env
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import Release, ReleaseProtocol

logger = setup_logger(__name__)

CACHE_FILE = Path(env.CONFIG_DIR) / "telegram_cache.json"
DEFAULT_CACHE_TTL = 7 * 24 * 60 * 60
_cache_lock = Lock()


def _coerce_cache_ttl(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return max(int(stripped), 0)
            except ValueError:
                return default
    return default


def _coerce_timestamp(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return float(stripped)
            except ValueError:
                return 0.0
    return 0.0


def _load_cache() -> dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load Telegram cache: %s", e)
    return {"entries": {}, "version": 1}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except OSError:
        logger.exception("Failed to save Telegram cache")


def _release_to_dict(release: Release) -> dict[str, Any]:
    data = asdict(release)
    if data.get("protocol"):
        data["protocol"] = (
            data["protocol"].value if hasattr(data["protocol"], "value") else str(data["protocol"])
        )
    return data


def _dict_to_release(data: dict[str, Any]) -> Release:
    if data.get("protocol"):
        try:
            data["protocol"] = ReleaseProtocol(data["protocol"])
        except (ValueError, KeyError):
            data["protocol"] = None
    return Release(**data)


def get_cached_results(
    cache_key: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any] | None:
    from shelfmark.core.config import config

    if ttl_seconds is None:
        ttl_value = config.get("TELEGRAM_CACHE_TTL", DEFAULT_CACHE_TTL)
        ttl_seconds = _coerce_cache_ttl(ttl_value, DEFAULT_CACHE_TTL)

    with _cache_lock:
        cache = _load_cache()
        entry = cache.get("entries", {}).get(cache_key)

        if not entry:
            return None

        cached_at = _coerce_timestamp(entry.get("cached_at", 0))
        age = time.time() - cached_at

        if ttl_seconds != 0 and age > ttl_seconds:
            return None

        releases = [_dict_to_release(r) for r in entry.get("releases", [])]

        logger.info(
            "Telegram cache hit for '%s' (%s releases, age: %.0fs)",
            entry.get("query", cache_key),
            len(releases),
            age,
        )

        return {
            "releases": releases,
            "cached_at": cached_at,
        }


def cache_results(
    cache_key: str,
    query: str,
    releases: list[Release],
) -> None:
    with _cache_lock:
        cache = _load_cache()

        if "entries" not in cache:
            cache["entries"] = {}

        cache["entries"][cache_key] = {
            "query": query,
            "releases": [_release_to_dict(r) for r in releases],
            "cached_at": time.time(),
        }

        _save_cache(cache)
        logger.info("Cached %s Telegram releases for '%s'", len(releases), query)


def clear_cache() -> int:
    with _cache_lock:
        cache = _load_cache()
        count = len(cache.get("entries", {}))
        cache["entries"] = {}
        _save_cache(cache)
        logger.info("Cleared %s Telegram cache entries", count)
        return count


def get_cache_stats() -> dict[str, Any]:
    from shelfmark.core.config import config

    ttl_value = config.get("TELEGRAM_CACHE_TTL", DEFAULT_CACHE_TTL)
    ttl_seconds = _coerce_cache_ttl(ttl_value, DEFAULT_CACHE_TTL)
    current_time = time.time()

    with _cache_lock:
        cache = _load_cache()
        entries = cache.get("entries", {})

        total = len(entries)
        expired = sum(
            1
            for entry in entries.values()
            if ttl_seconds != 0
            and current_time - _coerce_timestamp(entry.get("cached_at", 0)) > ttl_seconds
        )

        total_releases = sum(len(entry.get("releases", [])) for entry in entries.values())

        return {
            "total_entries": total,
            "expired_entries": expired,
            "valid_entries": total - expired,
            "total_releases": total_releases,
            "ttl_seconds": ttl_seconds,
            "cache_file": str(CACHE_FILE),
        }
