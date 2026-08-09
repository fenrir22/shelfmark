import json
import time
from pathlib import Path

from shelfmark.release_sources import Release, ReleaseProtocol
from shelfmark.release_sources.telegram import cache


def test_cache_and_retrieve_results(tmp_path, monkeypatch):
    cache_file = tmp_path / "telegram_cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", cache_file)

    release = Release(
        source="telegram",
        source_id="test123",
        title="Test Book",
        format="m4b",
        protocol=ReleaseProtocol.TELEGRAM,
        content_type="audiobook",
    )

    cache.cache_results("key1", "test query", [release])

    assert cache_file.exists()

    result = cache.get_cached_results("key1")
    assert result is not None
    assert len(result["releases"]) == 1
    assert result["releases"][0].title == "Test Book"
    assert result["releases"][0].source == "telegram"


def test_cache_expired_returns_none(tmp_path, monkeypatch):
    cache_file = tmp_path / "telegram_cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", cache_file)

    release = Release(
        source="telegram",
        source_id="test123",
        title="Old Book",
    )

    cache.cache_results("key1", "old query", [release])

    cache_data = json.loads(cache_file.read_text())
    cache_data["entries"]["key1"]["cached_at"] = time.time() - 100000
    cache_file.write_text(json.dumps(cache_data))

    result = cache.get_cached_results("key1", ttl_seconds=100)
    assert result is None


def test_cache_zero_ttl_never_expires(tmp_path, monkeypatch):
    cache_file = tmp_path / "telegram_cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", cache_file)

    release = Release(
        source="telegram",
        source_id="test123",
        title="Forever Book",
    )

    cache.cache_results("key1", "forever query", [release])

    cache_data = json.loads(cache_file.read_text())
    cache_data["entries"]["key1"]["cached_at"] = time.time() - 999999999
    cache_file.write_text(json.dumps(cache_data))

    result = cache.get_cached_results("key1", ttl_seconds=0)
    assert result is not None
    assert len(result["releases"]) == 1


def test_clear_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "telegram_cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", cache_file)

    cache.cache_results("key1", "query1", [])
    cache.cache_results("key2", "query2", [])

    count = cache.clear_cache()
    assert count == 2

    result = cache.get_cached_results("key1")
    assert result is None


def test_get_cache_stats(tmp_path, monkeypatch):
    cache_file = tmp_path / "telegram_cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", cache_file)

    release = Release(source="telegram", source_id="1", title="Book")
    cache.cache_results("key1", "query1", [release])
    cache.cache_results("key2", "query2", [release, release])

    stats = cache.get_cache_stats()
    assert stats["total_entries"] == 2
    assert stats["total_releases"] == 3
    assert stats["valid_entries"] == 2
    assert stats["expired_entries"] == 0


def test_cache_preserves_protocol(tmp_path, monkeypatch):
    cache_file = tmp_path / "telegram_cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", cache_file)

    release = Release(
        source="telegram",
        source_id="test123",
        title="Test",
        protocol=ReleaseProtocol.TELEGRAM,
    )

    cache.cache_results("key1", "query", [release])
    result = cache.get_cached_results("key1")

    assert result is not None
    assert result["releases"][0].protocol == ReleaseProtocol.TELEGRAM
