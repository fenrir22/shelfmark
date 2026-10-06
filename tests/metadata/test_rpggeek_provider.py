"""Tests for the RPGGeek metadata provider (token-gated, best-effort)."""

from shelfmark.metadata_providers import (
    MetadataSearchOptions,
    SearchType,
    get_provider,
)
from shelfmark.metadata_providers.rpggeek import (
    RPGGeekProvider,
    apply_alias,
    fetch_item,
    normalize_title,
    search_items,
)

SEARCH_XML = """<?xml version="1.0" encoding="utf-8"?>
<items total="2">
  <item type="rpgitem" id="312234" name="Dungeons &amp; Dragons Player's Handbook" yearpublished="2024" />
  <item type="rpgitem" id="123456" name="Unrelated Game Supplement" yearpublished="2020" />
</items>
"""

THING_XML = """<?xml version="1.0" encoding="utf-8"?>
<items>
  <item type="rpgitem" id="312234" yearpublished="2024">
    <thumbnail>https://cf.geekdo-images.com/thumb/img/abc123.jpg</thumbnail>
    <image>https://cf.geekdo-images.com/image/img/abc123.jpg</image>
    <name type="primary" value="Dungeons &amp; Dragons Player's Handbook" />
    <description>Core rulebook&amp;#10;for D&amp;D.</description>
  </item>
  <item type="rpgitem" id="123456" yearpublished="2020">
    <thumbnail>https://cf.geekdo-images.com/thumb/img/other.jpg</thumbnail>
    <name type="primary" value="Unrelated Game Supplement" />
  </item>
</items>
"""


def _mock_bgg(monkeypatch, calls):
    import shelfmark.metadata_providers.rpggeek as provider_module

    class FakeResponse:
        def __init__(self, text):
            self.content = text.encode()

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, headers=None, timeout=None, verify=None):
        calls.append((url, params, headers))
        if url.endswith("/search"):
            assert headers["Authorization"] == "Bearer test-token"
            assert params["type"] == "rpgitem"
            return FakeResponse(SEARCH_XML)
        assert url.endswith("/thing")
        return FakeResponse(THING_XML)

    monkeypatch.setattr(provider_module.requests, "get", fake_get)
    # No throttle waiting in tests.
    monkeypatch.setattr(provider_module, "MIN_CALL_GAP", 0)


def _options(**kwargs):
    kwargs.setdefault("query", "dnd")
    kwargs.setdefault("search_type", SearchType.GENERAL)
    return MetadataSearchOptions(**kwargs)


def test_normalize_and_alias():
    assert normalize_title("D&D_5e_PHB_ITA.pdf") == "d d 5e phb ita pdf"
    assert apply_alias(normalize_title("Manuale del Giocatore")) == (
        "dungeons dragons player s handbook"
    )
    assert apply_alias(normalize_title("Player's Handbook")) == "player s handbook"


def test_search_items(monkeypatch):
    calls = []
    _mock_bgg(monkeypatch, calls)

    items = search_items("dungeons dragons", "test-token")
    assert len(items) == 2
    assert items[0].id == "312234"
    assert items[0].name == "Dungeons & Dragons Player's Handbook"
    assert items[0].image == "https://cf.geekdo-images.com/image/img/abc123.jpg"
    assert items[0].year == 2024
    # Thumbnail fallback when no full image.
    assert items[1].image == "https://cf.geekdo-images.com/thumb/img/other.jpg"
    assert items[1].year == 2020
    # One search + one batched thing call.
    assert len(calls) == 2
    assert calls[1][1]["id"] == "312234,123456"


def test_search_items_no_token_no_calls(monkeypatch):
    import shelfmark.metadata_providers.rpggeek as provider_module

    def boom(*args, **kwargs):
        raise AssertionError("must not call network")

    monkeypatch.setattr(provider_module.requests, "get", boom)
    assert search_items("dnd", "") == []
    assert fetch_item("312234", "") is None


def test_provider_search_maps_books(monkeypatch):
    calls = []
    _mock_bgg(monkeypatch, calls)

    provider = RPGGeekProvider(api_token="test-token")
    assert provider.is_available() is True
    books = provider.search(_options(query="manuale del giocatore"))
    assert len(books) == 2
    book = books[0]
    assert book.provider == "rpggeek"
    assert book.provider_id == "312234"
    assert book.title == "Dungeons & Dragons Player's Handbook"
    assert book.cover_url == "https://cf.geekdo-images.com/image/img/abc123.jpg"
    assert book.publish_year == 2024
    assert book.source_url == "https://rpggeek.com/rpgitem/312234"
    # ISBN search unsupported, empty query empty result.
    assert provider.search(_options(query="", search_type=SearchType.GENERAL)) == []
    assert provider.search_by_isbn("9780786965627") is None


def test_provider_unavailable_without_token():
    provider = RPGGeekProvider(api_token="")
    assert provider.is_available() is False
    assert provider.search(_options()) == []
    assert provider.get_book("312234") is None


def test_provider_get_book(monkeypatch):
    calls = []
    _mock_bgg(monkeypatch, calls)

    provider = RPGGeekProvider(api_token="test-token")
    book = provider.get_book("312234")
    assert book is not None
    assert book.title == "Dungeons & Dragons Player's Handbook"
    assert provider.get_book("not-an-id") is None


def test_provider_registered():
    provider = get_provider("rpggeek")
    assert isinstance(provider, RPGGeekProvider)
    assert provider.display_name == "RPGGeek"
    assert provider.requires_auth is True


def test_provider_settings_registered():
    import shelfmark.metadata_providers.rpggeek  # noqa: F401
    from shelfmark.core.settings_registry import get_all_settings_tabs

    tabs = list(get_all_settings_tabs())
    assert any(getattr(tab, "name", "") == "rpggeek" for tab in tabs)


def test_test_connection_reports_token_problems(monkeypatch):
    from shelfmark.metadata_providers.rpggeek import _test_rpggeek_connection

    result = _test_rpggeek_connection({})
    assert result["success"] is False

    calls = []
    _mock_bgg(monkeypatch, calls)
    result = _test_rpggeek_connection({"RPGEEK_API_TOKEN": "test-token"})
    assert result["success"] is True
