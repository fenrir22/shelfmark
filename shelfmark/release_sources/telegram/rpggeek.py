"""Cover lookup for manuale results, backed by the RPGGeek provider.

Orchestration only: the BGG search itself lives in
:mod:`shelfmark.metadata_providers.rpggeek` (shared with the provider page).
Matched URLs are cached persistently in ``CONFIG_DIR/rpggeek_covers.json``
(hits 180 days, misses retried after 7 days).

Without an RPGGeek token everything here is a silent no-op: covers stay
empty but search keeps working. All failures are swallowed for the same
reason — covers are best-effort decoration.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any

from shelfmark.config import env
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers.rpggeek import (
    apply_alias,
    normalize_title,
    search_items,
)

logger = setup_logger(__name__)

CACHE_FILE = Path(env.CONFIG_DIR) / "rpggeek_covers.json"
HIT_TTL = 180 * 24 * 60 * 60
MISS_TTL = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 2000
MAX_CANDIDATES = 10
# Open Library fallback (no token): one call per title, keep it bounded.
MAX_FALLBACK_ITEMS = 8

_cache_lock = Lock()


def _get_token() -> str:
    value = config.get("RPGEEK_API_TOKEN", "")
    return str(value or "").strip()


def _load_cache() -> dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("entries", {})
                return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load RPGGeek cover cache: %s", exc)
    return {"entries": {}}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        entries = cache.get("entries", {})
        if len(entries) > MAX_CACHE_ENTRIES:
            ordered = sorted(entries.items(), key=lambda kv: kv[1].get("ts", 0))
            cache["entries"] = dict(ordered[-MAX_CACHE_ENTRIES:])
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except OSError as exc:
        logger.warning("Failed to save RPGGeek cover cache: %s", exc)


def _cached_url(normalized: str, now: float) -> str | None | bool:
    """Return cached URL, None for cached miss, or False when unknown/expired."""
    cache = _load_cache()
    entry = cache.get("entries", {}).get(normalized)
    if not isinstance(entry, dict):
        return False
    age = now - float(entry.get("ts", 0) or 0)
    url = entry.get("url")
    if url:
        return url if age < HIT_TTL else False
    return None if age < MISS_TTL else False


def _store_url(normalized: str, url: str | None) -> None:
    with _cache_lock:
        cache = _load_cache()
        cache.setdefault("entries", {})[normalized] = {"url": url, "ts": time.time()}
        _save_cache(cache)


def _best_image_for(normalized: str, images: dict[str, str]) -> str | None:
    if normalized in images:
        return images[normalized]
    for name, url in images.items():
        if name and (name in normalized or normalized in name):
            return url
    return None


def preview_for_title(title: object) -> str | None:
    """Cache-only cover lookup (no network). Used by get_record()."""
    normalized = normalize_title(title)
    if not normalized:
        return None
    now = time.time()
    hit = _cached_url(normalized, now)
    if isinstance(hit, str):
        return hit
    aliased = apply_alias(normalized)
    if aliased != normalized:
        hit = _cached_url(aliased, now)
        if isinstance(hit, str):
            return hit
    return None


def enrich_releases_with_covers(releases: list, query: str) -> int:
    """Attach ``extra['preview']`` covers to manuale releases. Returns count.

    RPGGeek when a token is configured, otherwise the free Open Library
    fallback. No releases, disabled covers, or any failure → 0 and releases
    untouched.
    """
    if not releases or not (query or "").strip():
        return 0
    if not bool(config.get("RPGEEK_COVERS_ENABLED", True)):
        return 0

    now = time.time()
    pending: list[tuple[int, str]] = []
    attached = 0
    for index, release in enumerate(releases):
        extra = getattr(release, "extra", None)
        if not isinstance(extra, dict) or extra.get("preview"):
            continue
        normalized = normalize_title(getattr(release, "title", ""))
        if not normalized:
            continue
        hit = _cached_url(normalized, now)
        if isinstance(hit, str):
            extra["preview"] = hit
            attached += 1
        elif hit is False:
            pending.append((index, normalized))

    if not pending:
        return attached

    if _get_token():
        return attached + _enrich_via_rpggeek(releases, query, pending)
    return attached + _enrich_via_openlibrary(releases, pending)


def _enrich_via_rpggeek(releases: list, query: str, pending: list[tuple[int, str]]) -> int:
    """RPGGeek path: at most 2 calls (search + batched thing)."""
    attached = 0
    try:
        # The catalogue is English: translate known Italian titles so the
        # search itself can match (e.g. "manuale del giocatore").
        items = search_items(
            apply_alias(normalize_title(query)), _get_token(), limit=MAX_CANDIDATES
        )
        images = {normalize_title(item.name): item.image for item in items if item.image}
    except Exception as exc:  # noqa: BLE001 - enrichment must never break a search
        logger.warning("RPGGeek cover enrichment failed: %s", exc)
        return attached

    for index, normalized in pending:
        url = _best_image_for(normalized, images) or _best_image_for(
            apply_alias(normalized), images
        )
        _store_url(normalized, url)
        if url:
            extra = getattr(releases[index], "extra", None)
            if isinstance(extra, dict):
                extra["preview"] = url
                attached += 1
    return attached


def _enrich_via_openlibrary(releases: list, pending: list[tuple[int, str]]) -> int:
    """Free fallback path (no token): one Open Library call per title."""
    from .openlibrary import fetch_cover_url

    attached = 0
    for index, normalized in pending[:MAX_FALLBACK_ITEMS]:
        # The catalogue is English: translate known Italian titles first.
        aliased = apply_alias(normalized)
        query_text = aliased if aliased != normalized else getattr(
            releases[index], "title", ""
        )
        try:
            url = fetch_cover_url(query_text, normalized, normalize_title)
        except Exception as exc:  # noqa: BLE001 - enrichment must never break a search
            logger.warning("Open Library cover enrichment failed: %s", exc)
            continue
        _store_url(normalized, url)
        if url:
            extra = getattr(releases[index], "extra", None)
            if isinstance(extra, dict):
                extra["preview"] = url
                attached += 1
    return attached
