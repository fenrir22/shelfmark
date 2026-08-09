from types import SimpleNamespace

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release, ReleaseProtocol
from shelfmark.release_sources.telegram.parser import TelegramParsedResult
from shelfmark.release_sources.telegram.source import TelegramSource


def test_convert_to_releases_basic():
    source = TelegramSource()

    parsed = [
        TelegramParsedResult(
            title="Dune",
            author="Frank Herbert",
            format="m4b",
            size="500MB",
            size_bytes=524288000,
            duration="21h 14m",
            message_id=42,
            chat_id=999,
            has_document=True,
            document_id="123456789",
            file_name="Dune.m4b",
            content_type="audiobook",
        ),
    ]

    releases = source._convert_to_releases(parsed, content_type="audiobook")

    assert len(releases) == 1
    release = releases[0]
    assert release.source == "telegram"
    assert release.title == "Dune"
    assert release.format == "m4b"
    assert release.size == "500MB"
    assert release.size_bytes == 524288000
    assert release.protocol == ReleaseProtocol.TELEGRAM
    assert release.indexer == "Telegram"
    assert release.content_type == "audiobook"
    assert release.extra["message_id"] == 42
    assert release.extra["chat_id"] == 999
    assert release.extra["document_id"] == "123456789"
    assert release.extra["has_document"] is True
    assert release.extra["file_name"] == "Dune.m4b"
    assert release.extra["duration"] == "21h 14m"
    assert release.extra["author"] == "Frank Herbert"


def test_convert_to_releases_without_document():
    source = TelegramSource()

    parsed = [
        TelegramParsedResult(
            title="Foundation",
            author="Isaac Asimov",
            format="epub",
            size="5MB",
            size_bytes=5242880,
            message_id=10,
            chat_id=100,
            has_document=False,
            content_type="ebook",
            callback_data="download_10",
        ),
    ]

    releases = source._convert_to_releases(parsed, content_type="ebook")

    assert len(releases) == 1
    release = releases[0]
    assert release.title == "Foundation"
    assert release.format == "epub"
    assert release.content_type == "ebook"
    assert release.extra["callback_data"] == "download_10"
    assert release.extra["has_document"] is False


def test_build_source_id_with_message_and_chat():
    source = TelegramSource()

    result = TelegramParsedResult(
        title="Test",
        message_id=42,
        chat_id=999,
        document_id="123",
    )

    source_id = source._build_source_id(result)
    assert isinstance(source_id, str)
    assert len(source_id) == 32


def test_build_source_id_without_ids():
    source = TelegramSource()

    result = TelegramParsedResult(title="Test Book")

    source_id = source._build_source_id(result)
    assert isinstance(source_id, str)
    assert len(source_id) == 32


def test_filter_by_content_type():
    releases = [
        Release(source="telegram", source_id="1", title="A", content_type="audiobook"),
        Release(source="telegram", source_id="2", title="B", content_type="ebook"),
        Release(source="telegram", source_id="3", title="C", content_type="audiobook"),
    ]

    filtered = TelegramSource._filter_by_content_type(releases, "audiobook")
    assert len(filtered) == 2
    assert all(r.content_type == "audiobook" for r in filtered)

    filtered = TelegramSource._filter_by_content_type(releases, "ebook")
    assert len(filtered) == 1
    assert filtered[0].content_type == "ebook"


def test_build_query_from_book_metadata():
    source = TelegramSource()

    book = BookMetadata(
        provider="test",
        provider_id="1",
        title="Dune",
        search_title="Dune",
        search_author="Frank Herbert",
        authors=["Frank Herbert"],
    )

    query = source._build_query(book)
    assert "Dune" in query
    assert "Frank Herbert" in query


def test_build_query_title_only():
    source = TelegramSource()

    book = BookMetadata(
        provider="test",
        provider_id="1",
        title="Dune",
        search_title="Dune",
    )

    query = source._build_query(book)
    assert query == "Dune"


def test_is_available_requires_enabled_and_connected(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramSource()

    monkeypatch.setattr(tg_source, "_config_bool", lambda key, default=False: key == "TELEGRAM_ENABLED")
    monkeypatch.setattr(tg_source, "_config_text", lambda key: "@testbot" if key == "TELEGRAM_BOT_USERNAME" else "")
    monkeypatch.setattr(tg_source.client_manager, "_connected", True)
    monkeypatch.setattr(tg_source.client_manager, "_client", object())

    assert source.is_available() is True


def test_is_available_disabled():
    import shelfmark.release_sources.telegram.source as tg_source
    from unittest.mock import patch

    source = TelegramSource()

    with patch.object(tg_source, "_config_bool", return_value=False):
        assert source.is_available() is False


def test_search_uses_cached_results(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramSource()
    cached_release = Release(
        source="telegram",
        source_id="cached",
        title="Cached Book",
        content_type="audiobook",
    )

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(
        tg_source,
        "_config_text",
        lambda key: "@testbot" if key == "TELEGRAM_BOT_USERNAME" else "",
    )
    monkeypatch.setattr(
        tg_source,
        "get_cached_results",
        lambda cache_key, *_args, **_kwargs: {"releases": [cached_release]},
    )
    monkeypatch.setattr(tg_source, "_emit_status", lambda *_args, **_kwargs: None)

    book = BookMetadata(provider="test", provider_id="123", title="Cached Book")
    plan = SimpleNamespace(primary_query="Cached Book")

    releases = source.search(book, plan, content_type="audiobook")

    assert releases == [cached_release]


def test_get_column_config():
    source = TelegramSource()
    config = source.get_column_config()

    assert len(config.columns) == 3
    column_keys = [col.key for col in config.columns]
    assert "format" in column_keys
    assert "size" in column_keys
    assert "extra.duration" in column_keys
    assert config.cache_ttl_seconds == 1800
    assert config.supported_filters == ["format"]
