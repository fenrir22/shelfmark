from types import SimpleNamespace

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import ReleaseProtocol
from shelfmark.release_sources.telegram.source import TelegramGroupSource


def _make_document_message(message_id=42, chat_id=999, file_name="Manuale.pdf", size=5242880):
    return SimpleNamespace(
        id=message_id,
        chat_id=chat_id,
        text="",
        document=SimpleNamespace(
            id=123,
            size=size,
            attributes=[SimpleNamespace(file_name=file_name)],
        ),
    )


def test_convert_messages_to_releases_basic():
    source = TelegramGroupSource()

    releases = source._convert_messages_to_releases([_make_document_message()])

    assert len(releases) == 1
    release = releases[0]
    assert release.source == "telegram_group"
    assert release.title == "Manuale.pdf"
    assert release.format == "pdf"
    assert release.size_bytes == 5242880
    assert release.size == "5.0 MB"
    assert release.protocol == ReleaseProtocol.TELEGRAM
    assert release.indexer == "Telegram Group"
    assert release.content_type == "manuale"
    assert release.extra["message_id"] == 42
    assert release.extra["chat_id"] == 999
    assert release.extra["document_id"] == "123"
    assert release.extra["has_document"] is True
    assert release.extra["file_name"] == "Manuale.pdf"


def test_convert_messages_to_releases_ebook_fallback():
    source = TelegramGroupSource()

    releases = source._convert_messages_to_releases(
        [_make_document_message()], content_type="ebook"
    )

    assert len(releases) == 1
    assert releases[0].content_type == "ebook"


def test_convert_messages_to_releases_skips_non_documents():
    source = TelegramGroupSource()

    text_message = SimpleNamespace(id=1, chat_id=999, text="just a text message", document=None)
    releases = source._convert_messages_to_releases([text_message])

    assert releases == []


def test_convert_messages_to_releases_skips_missing_chat():
    source = TelegramGroupSource()

    message = _make_document_message()
    del message.chat_id
    message.chat = SimpleNamespace(id=None)

    releases = source._convert_messages_to_releases([message])

    assert releases == []


def test_build_group_source_id():
    source_id = TelegramGroupSource._build_group_source_id(999, 42, "123")
    assert isinstance(source_id, str)
    assert len(source_id) == 32


def test_is_available_requires_enabled_and_connected(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()

    monkeypatch.setattr(
        tg_source,
        "_config_bool",
        lambda key, default=False: key == "TELEGRAM_GROUP_ENABLED",
    )
    monkeypatch.setattr(
        tg_source,
        "_config_text",
        lambda key: "@rpg_manuals" if key == "TELEGRAM_GROUP_USERNAME" else "",
    )
    monkeypatch.setattr(tg_source.client_manager, "_connected", True)
    monkeypatch.setattr(tg_source.client_manager, "_client", object())

    assert source.is_available() is True


def test_is_available_disabled():
    from unittest.mock import patch

    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()

    with patch.object(tg_source, "_config_bool", return_value=False):
        assert source.is_available() is False


def test_search_uses_cached_results(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()
    cached_release = SimpleNamespace(source="telegram_group", content_type="manuale")

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(
        tg_source,
        "_config_text",
        lambda key: "@rpg_manuals" if key == "TELEGRAM_GROUP_USERNAME" else "",
    )
    monkeypatch.setattr(
        tg_source,
        "get_cached_results",
        lambda cache_key, *_args, **_kwargs: {"releases": [cached_release]},
    )
    monkeypatch.setattr(tg_source, "_emit_status", lambda *_args, **_kwargs: None)

    book = BookMetadata(provider="test", provider_id="123", title="Dungeons and Dragons")
    plan = SimpleNamespace(primary_query="Dungeons and Dragons")

    releases = source.search(book, plan, content_type="manuale")

    assert releases == [cached_release]


def test_search_uses_numeric_topic_id(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()
    entity = object()

    def fake_config_text(key):
        if key == "TELEGRAM_GROUP_USERNAME":
            return "-1001503406491"
        if key == "TELEGRAM_GROUP_CHANNEL":
            return "1"
        return ""

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(tg_source, "_config_text", fake_config_text)
    monkeypatch.setattr(tg_source, "_config_int", lambda key, default=50: default)
    monkeypatch.setattr(tg_source, "get_cached_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "cache_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "_emit_status", lambda *a, **k: None)
    monkeypatch.setattr(tg_source.client_manager, "resolve_bot_entity", lambda g: entity)
    monkeypatch.setattr(tg_source.client_manager, "resolve_dialog_by_title", lambda t: None)
    monkeypatch.setattr(tg_source.client_manager, "find_forum_topic", lambda e, t: None)

    captured = {}

    def fake_search(entity_arg, query, limit=50, reply_to=None, offset_id=0):
        captured["reply_to"] = reply_to
        return []

    monkeypatch.setattr(tg_source.client_manager, "search_messages", fake_search)

    book = BookMetadata(provider="test", provider_id="123", title="Dune")
    plan = SimpleNamespace(primary_query="Dune")

    assert source.search(book, plan, content_type="manuale") == []
    assert captured["reply_to"] == 1


def test_search_parses_tme_link_topic(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()
    entity = object()
    resolved_entity = {}

    def fake_config_text(key):
        if key == "TELEGRAM_GROUP_USERNAME":
            return "https://t.me/c/1503406491/1"
        return ""

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(tg_source, "_config_text", fake_config_text)
    monkeypatch.setattr(tg_source, "_config_int", lambda key, default=50: default)
    monkeypatch.setattr(tg_source, "get_cached_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "cache_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "_emit_status", lambda *a, **k: None)

    def fake_resolve(entity_ref):
        resolved_entity["ref"] = entity_ref
        return entity

    monkeypatch.setattr(tg_source.client_manager, "resolve_bot_entity", fake_resolve)
    monkeypatch.setattr(tg_source.client_manager, "resolve_dialog_by_title", lambda t: None)

    captured = {}

    def fake_search(entity_arg, query, limit=50, reply_to=None, offset_id=0):
        captured["reply_to"] = reply_to
        return []

    monkeypatch.setattr(tg_source.client_manager, "search_messages", fake_search)

    book = BookMetadata(provider="test", provider_id="123", title="Dune")
    plan = SimpleNamespace(primary_query="Dune")

    assert source.search(book, plan, content_type="manuale") == []
    assert resolved_entity["ref"] == "-1001503406491"
    assert captured["reply_to"] == 1


def test_supported_content_types():
    assert set(TelegramGroupSource.supported_content_types) == {"manuale", "ebook"}


def test_search_targets_forum_topic(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()
    entity = object()

    def fake_config_text(key):
        if key == "TELEGRAM_GROUP_USERNAME":
            return "@amber_room"
        if key == "TELEGRAM_GROUP_CHANNEL":
            return "request and submission"
        return ""

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(tg_source, "_config_text", fake_config_text)
    monkeypatch.setattr(tg_source, "_config_int", lambda key, default=50: default)
    monkeypatch.setattr(tg_source, "get_cached_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "cache_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "_emit_status", lambda *a, **k: None)
    monkeypatch.setattr(tg_source.client_manager, "resolve_bot_entity", lambda g: entity)
    monkeypatch.setattr(tg_source.client_manager, "find_forum_topic", lambda e, t: 123)

    captured = {}

    def fake_search(entity_arg, query, limit=50, reply_to=None, offset_id=0):
        captured["reply_to"] = reply_to
        return []

    monkeypatch.setattr(tg_source.client_manager, "search_messages", fake_search)

    book = BookMetadata(provider="test", provider_id="123", title="Dune")
    plan = SimpleNamespace(primary_query="Dune")

    releases = source.search(book, plan, content_type="manuale")

    assert releases == []
    assert captured["reply_to"] == 123


def test_search_falls_back_to_dialog_title(monkeypatch):
    import shelfmark.release_sources.telegram.source as tg_source

    source = TelegramGroupSource()
    entity = object()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(
        tg_source,
        "_config_text",
        lambda key: "The Amber Room request and submissions"
        if key == "TELEGRAM_GROUP_USERNAME"
        else "",
    )
    monkeypatch.setattr(tg_source, "_config_int", lambda key, default=50: default)
    monkeypatch.setattr(tg_source, "get_cached_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "cache_results", lambda *a, **k: None)
    monkeypatch.setattr(tg_source, "_emit_status", lambda *a, **k: None)
    monkeypatch.setattr(tg_source.client_manager, "resolve_bot_entity", lambda g: None)
    monkeypatch.setattr(tg_source.client_manager, "resolve_dialog_by_title", lambda t: entity)
    monkeypatch.setattr(tg_source.client_manager, "find_forum_topic", lambda e, t: None)
    monkeypatch.setattr(tg_source.client_manager, "search_messages", lambda *a, **k: [])

    book = BookMetadata(provider="test", provider_id="123", title="Dune")
    plan = SimpleNamespace(primary_query="Dune")

    assert source.search(book, plan, content_type="manuale") == []


def test_search_skips_audiobook_content_type(monkeypatch):
    source = TelegramGroupSource()
    monkeypatch.setattr(source, "is_available", lambda: True)

    book = BookMetadata(provider="test", provider_id="123", title="Dune")
    plan = SimpleNamespace(primary_query="Dune")

    assert source.search(book, plan, content_type="audiobook") == []


def test_get_column_config():
    source = TelegramGroupSource()
    config = source.get_column_config()

    assert len(config.columns) == 2
    column_keys = [col.key for col in config.columns]
    assert "format" in column_keys
    assert "size" in column_keys
    assert config.supported_filters == ["format"]


def test_filter_messages_local_matches_text_and_file_name():
    from shelfmark.release_sources.telegram.client import TelegramClientManager

    manager = TelegramClientManager.__new__(TelegramClientManager)

    matching_text = SimpleNamespace(id=1, text="La storia di Warhammer", document=None)
    matching_file = SimpleNamespace(
        id=2,
        text="",
        document=SimpleNamespace(
            id=2,
            size=10,
            attributes=[SimpleNamespace(file_name="Warhammer_Old_World.rar")],
        ),
    )
    not_matching = SimpleNamespace(
        id=3,
        text="Call of Cthulhu",
        document=SimpleNamespace(
            id=3,
            size=10,
            attributes=[SimpleNamespace(file_name="CoC_Manuale.pdf")],
        ),
    )
    non_document = SimpleNamespace(id=4, text="nessuna corrispondenza", document=None)

    result = manager._filter_messages_local(
        [matching_text, matching_file, not_matching, non_document],
        "Warhammer",
        50,
    )

    assert [m.id for m in result] == [1, 2]


def test_filter_messages_local_applies_limit():
    from shelfmark.release_sources.telegram.client import TelegramClientManager

    manager = TelegramClientManager.__new__(TelegramClientManager)

    messages = [
        SimpleNamespace(
            id=i,
            text="",
            document=SimpleNamespace(
                id=i,
                size=10,
                attributes=[SimpleNamespace(file_name=f"Book {i}.pdf")],
            ),
        )
        for i in range(10)
    ]

    result = manager._filter_messages_local(messages, "book", 3)
    assert len(result) == 3
    assert [m.id for m in result] == [0, 1, 2]
