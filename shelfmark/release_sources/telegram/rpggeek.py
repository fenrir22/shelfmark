"""RPGGeek (BoardGameGeek family) cover lookup for manuals.

Used ONLY for ``manuale`` results of the Telegram group source: given the
user's free-text query, it searches RPG items (``type=rpgitem``), fetches the
top candidates in one batched ``thing`` call, and attaches the best image URL
to releases whose title matches.

The BGG XML API requires a registered application token (Bearer auth), set
via ``RPGEEK_API_TOKEN``. Without a token every function below is a silent
no-op: covers stay empty but search keeps working. All network failures are
swallowed for the same reason — covers are best-effort decoration.

Matched URLs are cached persistently in ``CONFIG_DIR/rpggeek_covers.json``
(hits 180 days, misses retried after 7 days) and BGG calls are throttled to
one every 5 seconds per their usage terms.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Lock
from typing import Any

import requests

from shelfmark.config import env
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

API_ROOT = "https://boardgamegeek.com/xmlapi2"
CACHE_FILE = Path(env.CONFIG_DIR) / "rpggeek_covers.json"
HIT_TTL = 180 * 24 * 60 * 60
MISS_TTL = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 2000
MAX_CANDIDATES = 10
REQUEST_TIMEOUT = 10
MIN_CALL_GAP = 5.0

_cache_lock = Lock()
_call_lock = Lock()
_last_call_time: float = 0.0


def _get_token() -> str:
    value = config.get("RPGEEK_API_TOKEN", "")
    return str(value or "").strip()


def _covers_enabled() -> bool:
    return bool(config.get("RPGEEK_COVERS_ENABLED", True)) and bool(_get_token())


def normalize_title(value: object) -> str:
    """Normalize a title for fuzzy comparison (lowercase, alnum only)."""
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Common Italian manual titles (as found in file names) mapped to the
# canonical English query to send to RPGGeek, whose catalogue is English.
# Keys and values are pre-normalized with normalize_title().
ITALIAN_TITLE_ALIASES = {
    "manuale del giocatore": "dungeons dragons player s handbook",
    "guida del dungeon master": "dungeon master s guide",
    "manuale del dungeon master": "dungeon master s guide",
    "manuale dei mostri": "monster manual",
    "guida di xanathar": "xanathar s guide to everything",
    "xanathar": "xanathar s guide to everything",
    "calderone di tasha": "tasha s cauldron of everything",
    "tasha": "tasha s cauldron of everything",
    "mordenkainen": "mordenkainen presents monsters of the multiverse",
    "guida di volo": "volo s guide to monsters",
    "volo": "volo s guide to monsters",
    "spada della costa": "sword coast adventurer s guide",
    "eberron": "eberron rising from the last war",
    "strahd": "curse of strahd",
}


def apply_alias(normalized: str) -> str:
    """Return the canonical English title when an Italian alias matches."""
    for alias, canonical in ITALIAN_TITLE_ALIASES.items():
        if alias in normalized:
            return canonical
    return normalized


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


def _bgg_get(path: str, params: dict[str, str]) -> ET.Element | None:
    """One throttled, authenticated BGG call. Returns the XML root or None."""
    global _last_call_time
    token = _get_token()
    if not token:
        return None
    with _call_lock:
        wait = MIN_CALL_GAP - (time.time() - _last_call_time)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(
                f"{API_ROOT}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/xml",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return ET.fromstring(response.content)
        except Exception as exc:  # noqa: BLE001 - covers are best-effort, any failure means no cover
            logger.warning("RPGGeek API call failed (%s): %s", path, exc)
            return None
        finally:
            _last_call_time = time.time()


def _search_ids(query: str) -> list[str]:
    root = _bgg_get("/search", {"query": query.strip(), "type": "rpgitem"})
    if root is None:
        return []
    ids = []
    for item in root.findall("item"):
        item_id = item.get("id")
        if item_id and len(ids) < MAX_CANDIDATES:
            ids.append(item_id)
    return ids


def _fetch_images(item_ids: list[str]) -> dict[str, str]:
    """Batch-fetch items; return {normalized name: image url}."""
    if not item_ids:
        return {}
    root = _bgg_get("/thing", {"id": ",".join(item_ids[:20]), "stats": "0"})
    if root is None:
        return {}
    images: dict[str, str] = {}
    for item in root.findall("item"):
        name_el = item.find("name[@type='primary']")
        if name_el is None:
            name_el = item.find("name")
        if name_el is None or not name_el.get("value"):
            continue
        image_el = item.find("image")
        thumb_el = item.find("thumbnail")
        url = None
        if image_el is not None and (image_el.text or "").strip():
            url = image_el.text.strip()
        elif thumb_el is not None and (thumb_el.text or "").strip():
            url = thumb_el.text.strip()
        if url:
            images[normalize_title(name_el.get("value"))] = url
    return images


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

    No token, no releases, or any failure → 0 and releases untouched.
    At most 2 BGG calls (search + batched thing) per invocation.
    """
    if not releases or not (query or "").strip():
        return 0
    if not _covers_enabled():
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

    try:
        # The catalogue is English: translate known Italian titles so the
        # search itself can match (e.g. "manuale del giocatore").
        images = _fetch_images(_search_ids(apply_alias(normalize_title(query))))
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
