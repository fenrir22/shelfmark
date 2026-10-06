"""Tests for the free Open Library cover fallback (no token needed)."""

import shelfmark.release_sources.telegram.openlibrary as ol
from shelfmark.metadata_providers.rpggeek import normalize_title
from shelfmark.release_sources.telegram.openlibrary import (
    clean_title_for_search,
    fetch_cover_url,
    titles_compatible,
)


def _docs(*entries):
    return {"docs": [{"title": title, "cover_i": cover} for title, cover in entries]}


def _mock_ol(monkeypatch, docs, calls):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return docs

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params)
        return FakeResponse()

    monkeypatch.setattr(ol.requests, "get", fake_get)
    monkeypatch.setattr(ol, "MIN_CALL_GAP", 0)


def test_clean_title_for_search():
    cleaned = clean_title_for_search("D&D_5e_Player's_Handbook_Manuale_del_Giocatore_HQ_09_2021")
    assert cleaned == "D&D 5e Player's Handbook Manuale del Giocatore"
    assert clean_title_for_search("ManualeCCE_v2 ITA.pdf") == "ManualeCCE"
    assert clean_title_for_search("HQ") == ""


def test_titles_compatible():
    ours = "d d 5e player s handbook manuale del giocatore"
    assert titles_compatible(ours, "dungeons dragons player s handbook") is False
    assert titles_compatible(ours, "player s handbook") is True
    assert titles_compatible(ours, "monster manual") is False
    assert titles_compatible(ours, "") is False
    assert titles_compatible("", "player s handbook") is False


def test_fetch_cover_url_match(monkeypatch):
    calls = []
    _mock_ol(
        monkeypatch,
        _docs(("Dungeons & Dragons Player's Handbook", 15223146)),
        calls,
    )
    title = "D&D_5e_Player's_Handbook_Manuale_del_Giocatore_HQ_09_2021"
    url = fetch_cover_url(title, normalize_title(title), normalize_title)
    assert url == "https://covers.openlibrary.org/b/id/15223146-M.jpg"
    assert calls[0]["q"] == "D&D 5e Player's Handbook Manuale del Giocatore"


def test_fetch_cover_url_rejects_wrong_book(monkeypatch):
    calls = []
    _mock_ol(monkeypatch, _docs(("Monster Manual", 999)), calls)
    url = fetch_cover_url(
        "Manuale del Giocatore",
        "manuale del giocatore",
        lambda value: value.lower(),
    )
    assert url is None


def test_fetch_cover_url_survives_failure(monkeypatch):
    import requests

    def failing_get(*args, **kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr(ol.requests, "get", failing_get)
    assert fetch_cover_url("Player's Handbook", "player s handbook", str) is None
