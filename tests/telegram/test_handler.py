from shelfmark.release_sources.telegram.handler import TelegramDownloadHandler


def test_build_retry_resolution_fields():
    handler = TelegramDownloadHandler()

    release_data = {
        "source": "telegram",
        "source_id": "abc123",
        "title": "Dune",
        "extra": {
            "message_id": 42,
            "chat_id": 999,
            "file_name": "Dune.m4b",
        },
    }

    result = handler.build_retry_resolution_fields(release_data)

    assert "retry_source_context" in result
    ctx = result["retry_source_context"]
    assert ctx["message_id"] == 42
    assert ctx["chat_id"] == 999
    assert ctx["file_name"] == "Dune.m4b"


def test_build_retry_resolution_fields_missing_extra():
    handler = TelegramDownloadHandler()

    release_data = {
        "source": "telegram",
        "source_id": "abc123",
        "title": "Dune",
    }

    result = handler.build_retry_resolution_fields(release_data)

    assert "retry_source_context" in result
    ctx = result["retry_source_context"]
    assert ctx["message_id"] is None
    assert ctx["chat_id"] is None
    assert ctx["file_name"] is None


def test_cancel_returns_true():
    handler = TelegramDownloadHandler()
    assert handler.cancel("any-task-id") is True


def test_download_fails_when_not_connected():
    handler = TelegramDownloadHandler()

    import shelfmark.release_sources.telegram.handler as tg_handler

    tg_handler.client_manager._connected = False
    tg_handler.client_manager._client = None

    statuses = []

    def status_callback(status, message):
        statuses.append((status, message))

    result = handler.download(
        task=None,
        cancel_flag=None,
        progress_callback=lambda p: None,
        status_callback=status_callback,
    )

    assert result is None
    assert any(s[0] == "failed" for s in statuses)
