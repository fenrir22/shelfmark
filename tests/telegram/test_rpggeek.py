"""Tests for the RPGGeek cover lookup (manuali only, best-effort)."""

from types import SimpleNamespace

import shelfmark.release_sources.telegram.rpggeek as rpggeek
from shelfmark.release_sources.telegram.rpggeek import (
    enrich_releases_with_covers,
    normalize_title,
    preview_for_title,
)

SEARCH_XML = """<?xml version="1.0" encoding="utf-8"?>
<items total="2">
  <item type="rpgitem" id="312234" name="Dungeons &amp; Dragons Player's Handbook" yearpublished="2024" />
  <item type="rpgitem" id="123456" name="Unrelated Game Supplement" yearpublished="2020" />
</items>
"""

THING_XML = """<?xml version="1.0" encoding="utf-8"?>
<items>
  <item type="rpgitem" id="312234">
    <thumbnail>https://cf.geekdo-images.com/thumb/img/abc123.jpg</thumbnail>
    <image>https://cf.geekdo-images.com/image/img/abc123.jpg</image>
    <name type="primary" value="Dungeons &amp; Dragons Player's Handbook" />
  </item>
  <item type="rpgitem" id="123456">
    <thumbnail>https://cf.geekdo-images.com/thumb/img/other.jpg</thumbnail>
    <name type="primary" value="Unrelated Game Supplement" />
  </item>
</items>
"""


def _make_release(title):
    return SimpleNamespace(title=title, extra={})


def _with_token(monkeypatch, tmp_path, token="test-token"):
    monkeypatch.setattr(rpggeek, "CACHE_FILE", tmp_path / "rpggeek_covers.json")
    fake_config = SimpleNamespace(
        get=lambda key, default=None: {
            "RPGEEK_API_TOKEN": token,
            "RPGEEK_COVERS_ENABLED": True,
        }.get(key, default)
    )
    monkeypatch.setattr(rpggeek, "config", fake_config)
    # No throttle waiting in tests.
    monkeypatch.setattr(rpggeek, "MIN_CALL_GAP", 0)


def _mock_bgg(monkeypatch, calls):
    class FakeResponse:
        def __init__(self, text):
            self.content = text.encode()

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        if url.endswith("/search"):
            assert headers["Authorization"] == "Bearer test-token"
            assert params["type"] == "rpgitem"
            return FakeResponse(SEARCH_XML)
        assert url.endswith("/thing")
        assert params["id"] == "312234,123456"
        return FakeResponse(THING_XML)

    monkeypatch.setattr(rpggeek.requests, "get", fake_get)


def test_normalize_title():
    assert normalize_title("DND5e_PHB_ITA.pdf") == "dnd5e phb ita pdf"
    assert normalize_title("  Player's  Handbook! ") == "player s handbook"
    assert normalize_title(None) == ""


def test_enrich_skipped_without_token(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path, token="")
    releases = [_make_release("Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd") == 0
    assert "preview" not in releases[0].extra


def test_enrich_attaches_matching_cover(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    calls = []
    _mock_bgg(monkeypatch, calls)

    releases = [_make_release("Dungeons & Dragons Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd players handbook") == 1
    assert releases[0].extra["preview"] == "https://cf.geekdo-images.com/image/img/abc123.jpg"
    # One search + one batched thing call.
    assert len(calls) == 2


def test_enrich_caches_miss_and_reuses_cache(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    calls = []
    _mock_bgg(monkeypatch, calls)

    releases = [
        _make_release("Dungeons & Dragons Player's Handbook"),
        _make_release("Some Obscure Zine Vol 3"),
    ]
    assert enrich_releases_with_covers(releases, "obscure zine") == 1
    assert len(calls) == 2
    assert "preview" not in releases[1].extra

    # Second run: everything served from cache, no HTTP at all.
    calls.clear()

    def boom(*args, **kwargs):
        raise AssertionError("must not call network")

    monkeypatch.setattr(rpggeek.requests, "get", boom)
    known = [_make_release("Dungeons & Dragons Player's Handbook")]
    assert enrich_releases_with_covers(known, "dnd") == 1
    assert known[0].extra["preview"].endswith("abc123.jpg")

    # Cached miss stays a miss without network.
    unknown2 = [_make_release("Some Obscure Zine Vol 3")]
    assert enrich_releases_with_covers(unknown2, "obscure zine") == 0


def test_enrich_survives_api_failure(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)

    def failing_get(*args, **kwargs):
        raise TimeoutError("slow api")

    monkeypatch.setattr(rpggeek.requests, "get", failing_get)
    releases = [_make_release("Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd") == 0
    assert "preview" not in releases[0].extra


def test_preview_for_title_cache_only(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    rpggeek._store_url(normalize_title("Player's Handbook"), "https://img.example/x.jpg")
    assert preview_for_title("Player's Handbook") == "https://img.example/x.jpg"
    assert preview_for_title("Unknown Thing") is None
    assert preview_for_title("") is None


def test_enrich_ignores_releases_with_preview(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    calls = []
    _mock_bgg(monkeypatch, calls)
    releases = [_make_release("Dungeons & Dragons Player's Handbook")]
    releases[0].extra["preview"] = "https://img.example/keep.jpg"
    assert enrich_releases_with_covers(releases, "dnd") == 0
    assert releases[0].extra["preview"] == "https://img.example/keep.jpg"
    assert calls == []
