"""Tests for the manuale cover orchestration (best-effort, cache-first)."""

from types import SimpleNamespace

import shelfmark.release_sources.telegram.rpggeek as rpggeek
from shelfmark.release_sources.telegram.rpggeek import (
    enrich_releases_with_covers,
    preview_for_title,
)


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


def _item(name, image):
    return SimpleNamespace(id="1", name=name, image=image, year=None, description=None)


def test_enrich_skipped_without_token(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path, token="")

    def boom(*args, **kwargs):
        raise AssertionError("must not call network")

    monkeypatch.setattr(rpggeek, "search_items", boom)
    import shelfmark.release_sources.telegram.openlibrary as ol_module

    monkeypatch.setattr(ol_module, "fetch_cover_url", boom)
    releases = [_make_release("Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd") == 0
    assert "preview" not in releases[0].extra


def test_enrich_falls_back_to_openlibrary_without_token(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path, token="")

    def boom(*args, **kwargs):
        raise AssertionError("must not call RPGGeek")

    monkeypatch.setattr(rpggeek, "search_items", boom)
    import shelfmark.release_sources.telegram.openlibrary as ol_module

    monkeypatch.setattr(
        ol_module, "fetch_cover_url", lambda *a, **k: "https://ol.example/cover.jpg"
    )
    releases = [_make_release("Manuale del Giocatore")]
    assert enrich_releases_with_covers(releases, "manuale del giocatore") == 1
    assert releases[0].extra["preview"] == "https://ol.example/cover.jpg"


def test_enrich_prefers_rpggeek_with_token(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("must not call Open Library")

    import shelfmark.release_sources.telegram.openlibrary as ol_module

    monkeypatch.setattr(ol_module, "fetch_cover_url", boom)

    def fake_search_items(query, token, limit=10, strict=False):
        return [_item("Dungeons & Dragons Player's Handbook", "https://img.example/phb.jpg")]

    monkeypatch.setattr(rpggeek, "search_items", fake_search_items)
    releases = [_make_release("Dungeons & Dragons Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd players handbook") == 1
    assert releases[0].extra["preview"] == "https://img.example/phb.jpg"


def test_enrich_attaches_matching_cover(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    calls = []

    def fake_search_items(query, token, limit=10, strict=False):
        calls.append((query, token))
        assert token == "test-token"
        return [_item("Dungeons & Dragons Player's Handbook", "https://img.example/phb.jpg")]

    monkeypatch.setattr(rpggeek, "search_items", fake_search_items)

    releases = [_make_release("Dungeons & Dragons Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd players handbook") == 1
    assert releases[0].extra["preview"] == "https://img.example/phb.jpg"
    assert len(calls) == 1


def test_enrich_uses_alias_for_search_and_match(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    calls = []

    def fake_search_items(query, token, limit=10, strict=False):
        calls.append(query)
        return [_item("Dungeons & Dragons Player's Handbook", "https://img.example/phb.jpg")]

    monkeypatch.setattr(rpggeek, "search_items", fake_search_items)

    releases = [_make_release("Manuale del Giocatore")]
    assert enrich_releases_with_covers(releases, "manuale del giocatore") == 1
    assert releases[0].extra["preview"] == "https://img.example/phb.jpg"
    # The provider itself was queried with the English alias.
    assert calls == ["dungeons dragons player s handbook"]


def test_enrich_caches_miss_and_reuses_cache(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)

    def fake_search_items(query, token, limit=10, strict=False):
        return [_item("Dungeons & Dragons Player's Handbook", "https://img.example/phb.jpg")]

    monkeypatch.setattr(rpggeek, "search_items", fake_search_items)

    releases = [
        _make_release("Dungeons & Dragons Player's Handbook"),
        _make_release("Some Obscure Zine Vol 3"),
    ]
    assert enrich_releases_with_covers(releases, "obscure zine") == 1
    assert "preview" not in releases[1].extra

    # Second run: everything served from cache, provider never called.
    def boom(*args, **kwargs):
        raise AssertionError("must not call provider")

    monkeypatch.setattr(rpggeek, "search_items", boom)
    known = [_make_release("Dungeons & Dragons Player's Handbook")]
    assert enrich_releases_with_covers(known, "dnd") == 1
    assert known[0].extra["preview"] == "https://img.example/phb.jpg"

    # Cached miss stays a miss without network.
    unknown2 = [_make_release("Some Obscure Zine Vol 3")]
    assert enrich_releases_with_covers(unknown2, "obscure zine") == 0


def test_enrich_survives_provider_failure(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)

    def failing_search(*args, **kwargs):
        raise TimeoutError("slow api")

    monkeypatch.setattr(rpggeek, "search_items", failing_search)
    releases = [_make_release("Player's Handbook")]
    assert enrich_releases_with_covers(releases, "dnd") == 0
    assert "preview" not in releases[0].extra


def test_transport_failure_is_not_cached_as_miss(monkeypatch, tmp_path):
    """A network blip must not poison the cache for days."""
    _with_token(monkeypatch, tmp_path, token="")

    import shelfmark.release_sources.telegram.openlibrary as ol_module

    def failing_fetch(*args, **kwargs):
        raise TimeoutError("blip")

    monkeypatch.setattr(ol_module, "fetch_cover_url", failing_fetch)
    releases = [_make_release("Manuale del Giocatore")]
    assert enrich_releases_with_covers(releases, "manuale del giocatore") == 0

    cache = rpggeek._load_cache()
    assert cache.get("entries", {}) == {}


def test_preview_for_title_cache_only(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)
    rpggeek._store_url("player s handbook", "https://img.example/x.jpg")
    assert preview_for_title("Player's Handbook") == "https://img.example/x.jpg"
    assert preview_for_title("Unknown Thing") is None
    assert preview_for_title("") is None


def test_enrich_ignores_releases_with_preview(monkeypatch, tmp_path):
    _with_token(monkeypatch, tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("must not call provider")

    monkeypatch.setattr(rpggeek, "search_items", boom)
    releases = [_make_release("Dungeons & Dragons Player's Handbook")]
    releases[0].extra["preview"] = "https://img.example/keep.jpg"
    assert enrich_releases_with_covers(releases, "dnd") == 0
    assert releases[0].extra["preview"] == "https://img.example/keep.jpg"
