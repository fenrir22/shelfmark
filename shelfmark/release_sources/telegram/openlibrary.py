"""Free Open Library cover fallback for manuals (no token needed).

Used ONLY when no RPGGeek token is configured: given a cleaned release
title, it queries Open Library's public search and returns the first
plausibly-matching cover (``covers.openlibrary.org``). Best-effort like
everything cover-related: any failure means no cover, never a broken search.
"""

from __future__ import annotations

import re
import time
from threading import Lock
from typing import TYPE_CHECKING

import requests

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = setup_logger(__name__)

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
REQUEST_TIMEOUT = 10
MIN_CALL_GAP = 1.0
MAX_RESULTS = 5

_call_lock = Lock()
_last_call_time: float = 0.0

# Noise tokens commonly found in file names (years and pure numbers are
# handled separately). Compared case-insensitively against whole words.
NOISE_TOKENS = frozenset(
    {
        "hq",
        "lq",
        "mq",
        "ita",
        "eng",
        "en",
        "it",
        "pdf",
        "epub",
        "mobi",
        "azw3",
        "fb2",
        "djvu",
        "cbz",
        "cbr",
        "zip",
        "rar",
        "v1",
        "v2",
        "v3",
        "ed",
        "edizione",
        "edition",
        "scan",
        "scansione",
        "ocr",
        "retail",
        "ebook",
        "completo",
        "completa",
        "full",
    }
)

# Small words ignored when checking title compatibility.
STOPWORDS = frozenset(
    {
        "the",
        "and",
        "of",
        "a",
        "del",
        "della",
        "dei",
        "degli",
        "delle",
        "al",
        "allo",
        "alla",
        "dai",
        "dagli",
        "dalle",
        "dal",
        "dall",
        "il",
        "lo",
        "la",
        "gli",
        "le",
        "un",
        "una",
        "uno",
        "de",
        "di",
        "da",
        "in",
        "con",
        "per",
    }
)


def clean_title_for_search(stem: object) -> str:
    """Turn a file-name stem into a plausible book query.

    Example: ``D&D_5e_Player's_Handbook_Manuale_del_Giocatore_HQ_09_2021``
    becomes ``D&D 5e Player's Handbook Manuale del Giocatore``.
    """
    text = re.sub(r"[_\-+.]+", " ", str(stem or ""))
    words = []
    for word in text.split():
        stripped = word.strip("()[]{}.,;:!?\"'")
        lowered = stripped.lower()
        if not lowered:
            continue
        if lowered in NOISE_TOKENS:
            continue
        if re.fullmatch(r"(19|20)\d{2}", lowered):
            continue
        if re.fullmatch(r"\d+(st|nd|rd|th)?", lowered):
            continue
        words.append(stripped)
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def _significant_words(normalized: str) -> list[str]:
    return [
        word
        for word in normalized.split()
        if len(word) > 2 and word not in STOPWORDS and not word.isdigit()
    ]


def titles_compatible(ours_normalized: str, theirs_normalized: str) -> bool:
    """True when every significant word of the candidate appears in ours.

    Guards against attaching a plausible-but-wrong cover (e.g. another book
    by the same author): no cover is better than a wrong cover.
    """
    wanted = _significant_words(theirs_normalized)
    if not wanted:
        return False
    ours = set(ours_normalized.split())
    return all(word in ours for word in wanted)


def fetch_cover_url(
    query: str, ours_normalized: str, normalize: Callable[[object], str]
) -> str | None:
    """Return an Open Library cover URL for the query, or None."""
    global _last_call_time
    cleaned = clean_title_for_search(query)
    if not cleaned:
        return None
    with _call_lock:
        wait = MIN_CALL_GAP - (time.time() - _last_call_time)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(
                SEARCH_URL,
                params={
                    "title": cleaned,
                    "limit": str(MAX_RESULTS),
                    "fields": "key,title,author_name,cover_i",
                    "language": "eng",
                },
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            docs = response.json().get("docs", [])
        except requests.RequestException as exc:
            logger.warning("Open Library cover search failed: %s", exc)
            return None
        except ValueError as exc:
            logger.warning("Open Library returned invalid JSON: %s", exc)
            return None
        finally:
            _last_call_time = time.time()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        cover_id = doc.get("cover_i")
        if not cover_id:
            continue
        candidate = normalize(doc.get("title", ""))
        if candidate and titles_compatible(ours_normalized, candidate):
            return COVER_URL.format(cover_id=cover_id)
    return None
