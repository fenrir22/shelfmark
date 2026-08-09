"""Tests for the Telegram download wait/stall behaviour.

Regression test: downloads used to be aborted by a hard 600s wall-clock
timeout, killing slow-but-active multi-gigabyte transfers around ~80%.
Downloads now wait as long as progress is being made and only abort on a
stall (no progress for ``idle_timeout`` seconds) or cancellation.
"""

import threading
from unittest.mock import patch

import pytest

from shelfmark.release_sources.telegram.client import TelegramClientManager


@pytest.fixture(autouse=True)
def _reset_singleton():
    TelegramClientManager._instance = None
    yield
    TelegramClientManager._instance = None


def _make_client() -> TelegramClientManager:
    client = TelegramClientManager()
    client._connected = True
    client._client = object()
    return client


def test_long_active_download_not_killed_by_wall_clock():
    client = _make_client()

    captured = {"progress": None}
    polls = {"count": 0}
    clock = {"now": 1000.0}

    async def fake_download(message, output_path, progress_callback=None, cancel_flag=None):
        captured["progress"] = progress_callback
        return "/tmp/downloaded.m4b"

    client._download_media_async = fake_download  # type: ignore[method-assign]

    def fake_monotonic():
        return clock["now"]

    def fake_run_coro_threadsafe(coro, loop):
        captured["progress"] = coro.cr_frame.f_locals["progress_callback"]

        class FakeFuture:
            def result(self, timeout):
                polls["count"] += 1
                clock["now"] += 2.0
                if captured["progress"] is not None:
                    captured["progress"](1, 100)
                if polls["count"] > 400:
                    return "/tmp/downloaded.m4b"
                raise TimeoutError

            def cancel(self):
                return True

        return FakeFuture()

    with (
        patch("shelfmark.release_sources.telegram.client.time.monotonic", side_effect=fake_monotonic),
        patch(
            "shelfmark.release_sources.telegram.client.asyncio.run_coroutine_threadsafe",
            side_effect=fake_run_coro_threadsafe,
        ),
    ):
        result = client.download_media("msg", "/tmp/out", idle_timeout=1.0)

    assert result == "/tmp/downloaded.m4b"
    # Survived far beyond the old 600s wall-clock cap (~800s simulated).
    assert polls["count"] > 300


def test_download_aborts_when_no_progress():
    client = _make_client()
    clock = {"now": 1000.0}
    cancelled = {"flag": False}

    async def fake_download(message, output_path, progress_callback=None, cancel_flag=None):
        return "/tmp/downloaded.m4b"

    client._download_media_async = fake_download  # type: ignore[method-assign]

    def fake_monotonic():
        return clock["now"]

    def fake_run_coro_threadsafe(coro, loop):
        class FakeFuture:
            def result(self, timeout):
                clock["now"] += 2.0
                raise TimeoutError

            def cancel(self):
                cancelled["flag"] = True
                return True

        return FakeFuture()

    with (
        patch("shelfmark.release_sources.telegram.client.time.monotonic", side_effect=fake_monotonic),
        patch(
            "shelfmark.release_sources.telegram.client.asyncio.run_coroutine_threadsafe",
            side_effect=fake_run_coro_threadsafe,
        ),
    ):
        result = client.download_media("msg", "/tmp/out", idle_timeout=1.0)

    assert result is None
    assert cancelled["flag"] is True


def test_download_cancelled_returns_none():
    client = _make_client()
    cancel_flag = threading.Event()
    cancel_flag.set()
    polls = {"count": 0}

    async def fake_download(message, output_path, progress_callback=None, cancel_flag=None):
        return "/tmp/downloaded.m4b"

    client._download_media_async = fake_download  # type: ignore[method-assign]

    def fake_run_coro_threadsafe(coro, loop):
        class FakeFuture:
            def result(self, timeout):
                polls["count"] += 1
                # Simulate the async loop observing cancel_flag and unwinding.
                if polls["count"] >= 2:
                    return None
                raise TimeoutError

            def cancel(self):
                return True

        return FakeFuture()

    with patch(
        "shelfmark.release_sources.telegram.client.asyncio.run_coroutine_threadsafe",
        side_effect=fake_run_coro_threadsafe,
    ):
        result = client.download_media("msg", "/tmp/out", cancel_flag=cancel_flag, idle_timeout=1.0)

    assert result is None
