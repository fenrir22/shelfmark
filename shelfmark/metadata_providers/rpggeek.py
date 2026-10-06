"""RPGGeek (BoardGameGeek family) metadata provider. Requires API token.

RPGGeek is the reference catalogue for tabletop role-playing games and
their manuals. The BGG XML API requires a registered application token
(Bearer auth, ``boardgamegeek.com/applications``); without one every method
below returns empty/None instead of raising.

The provider is also the shared home of the BGG search helpers reused for
manual cover lookup (Italian title aliases included).
"""

from __future__ import annotations

import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, ClassVar

import requests

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SettingsField,
    register_settings,
)
from shelfmark.download.network import get_ssl_verify
from shelfmark.metadata_providers import (
    BookMetadata,
    MetadataProvider,
    MetadataSearchOptions,
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
    register_provider_kwargs,
)

logger = setup_logger(__name__)

BGG_API_ROOT = "https://boardgamegeek.com/xmlapi2"
REQUEST_TIMEOUT = 15
# BGG usage terms ask for restraint between calls.
MIN_CALL_GAP = 5.0
MAX_CANDIDATES = 10
MAX_THING_BATCH = 20

_call_lock = threading.Lock()
_last_call_time: float = 0.0


@dataclass
class RpgItem:
    """One RPGGeek catalogue entry."""

    id: str
    name: str
    image: str | None = None
    year: int | None = None
    description: str | None = None


def normalize_title(value: object) -> str:
    """Normalize a title for fuzzy comparison (lowercase, alnum only)."""
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Common Italian manual titles (as found in file names) mapped to the
# canonical English query to send to RPGGeek, whose catalogue is English.
# Keys and values are pre-normalized with normalize_title().
ITALIAN_TITLE_ALIASES = {
    "manuale del giocatore": "dungeons dragons player s handbook",
    "guida del dungeon master": "dungeon master s guide",
    "manuale del dungeon master": "dungeon master s guide",
    "manuale dei mostri": "monster manual",
    "guida di xanathar": "xanathar s guide to everything",
    "xanathar": "xanathar s guide to everything",
    "calderone di tasha": "tasha s cauldron of everything",
    "tasha": "tasha s cauldron of everything",
    "mordenkainen": "mordenkainen presents monsters of the multiverse",
    "guida di volo": "volo s guide to monsters",
    "volo": "volo s guide to monsters",
    "spada della costa": "sword coast adventurer s guide",
    "eberron": "eberron rising from the last war",
    "strahd": "curse of strahd",
}


def apply_alias(normalized: str) -> str:
    """Return the canonical English title when an Italian alias matches."""
    for alias, canonical in ITALIAN_TITLE_ALIASES.items():
        if alias in normalized:
            return canonical
    return normalized


def _get_token(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    return str(app_config.get("RPGEEK_API_TOKEN", "") or "").strip()


def _authed_get(path: str, params: dict[str, str], api_token: str) -> ET.Element | None:
    """One throttled, authenticated BGG call. Returns the XML root or None."""
    global _last_call_time
    with _call_lock:
        wait = MIN_CALL_GAP - (time.time() - _last_call_time)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(
                f"{BGG_API_ROOT}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Accept": "application/xml",
                },
                timeout=REQUEST_TIMEOUT,
                verify=get_ssl_verify(BGG_API_ROOT),
            )
            response.raise_for_status()
            return ET.fromstring(response.content)
        except requests.Timeout:
            logger.warning("RPGGeek API call timed out (%s)", path)
            return None
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == HTTPStatus.UNAUTHORIZED:
                logger.warning(
                    "RPGGeek API rejected the token (401). Register an application at "
                    "boardgamegeek.com/applications and update RPGEEK_API_TOKEN."
                )
            else:
                logger.warning("RPGGeek API HTTP error (%s): %s", path, exc)
            return None
        except requests.RequestException as exc:
            logger.warning("RPGGeek API request failed (%s): %s", path, exc)
            return None
        except ET.ParseError as exc:
            logger.warning("RPGGeek API returned invalid XML (%s): %s", path, exc)
            return None
        finally:
            _last_call_time = time.time()


def _search_ids(query: str, api_token: str) -> list[str]:
    root = _authed_get("/search", {"query": query.strip(), "type": "rpgitem"}, api_token)
    if root is None:
        return []
    ids = []
    for item in root.findall("item"):
        item_id = item.get("id")
        if item_id and len(ids) < MAX_CANDIDATES:
            ids.append(item_id)
    return ids


def _parse_year(value: object) -> int | None:
    try:
        year = int(str(value or "").strip())
    except TypeError, ValueError:
        return None
    return year if 1900 <= year <= 2100 else None


def _parse_item(element: ET.Element) -> RpgItem | None:
    item_id = element.get("id")
    name_el = element.find("name[@type='primary']")
    if name_el is None:
        name_el = element.find("name")
    if not item_id or name_el is None or not (name_el.get("value") or "").strip():
        return None
    image_url = None
    image_el = element.find("image")
    thumb_el = element.find("thumbnail")
    if image_el is not None and (image_el.text or "").strip():
        image_url = image_el.text.strip()
    elif thumb_el is not None and (thumb_el.text or "").strip():
        image_url = thumb_el.text.strip()
    description = None
    desc_el = element.find("description")
    if desc_el is not None and (desc_el.text or "").strip():
        description = html.unescape(desc_el.text.strip())
    return RpgItem(
        id=item_id,
        name=name_el.get("value", "").strip(),
        image=image_url,
        year=_parse_year(element.get("yearpublished")),
        description=description,
    )


def search_items(query: str, api_token: str, *, limit: int = MAX_CANDIDATES) -> list[RpgItem]:
    """Search RPG items (1 search + 1 batched thing call). Empty on failure."""
    token = _get_token(api_token)
    if not token or not (query or "").strip():
        return []
    ids = _search_ids(query.strip(), token)[: min(max(limit, 1), MAX_THING_BATCH)]
    if not ids:
        return []
    root = _authed_get("/thing", {"id": ",".join(ids), "stats": "0"}, token)
    if root is None:
        return []
    items = []
    for element in root.findall("item"):
        parsed = _parse_item(element)
        if parsed is not None:
            items.append(parsed)
    return items


def fetch_item(item_id: str, api_token: str) -> RpgItem | None:
    """Fetch one catalogue entry by id. None when missing or on failure."""
    token = _get_token(api_token)
    cleaned = re.sub(r"\D", "", str(item_id or ""))
    if not token or not cleaned:
        return None
    root = _authed_get("/thing", {"id": cleaned, "stats": "0"}, token)
    if root is None:
        return None
    for element in root.findall("item"):
        parsed = _parse_item(element)
        if parsed is not None:
            return parsed
    return None


def _item_to_book(item: RpgItem) -> BookMetadata:
    return BookMetadata(
        provider="rpggeek",
        provider_id=item.id,
        provider_display_name="RPGGeek",
        title=item.name,
        search_title=item.name,
        authors=[],
        cover_url=item.image,
        description=item.description,
        publish_year=item.year,
        source_url=f"https://rpggeek.com/rpgitem/{item.id}",
    )


@register_provider_kwargs("rpggeek")
def _rpggeek_kwargs() -> dict[str, Any]:
    return {"api_token": app_config.get("RPGEEK_API_TOKEN", "")}


@register_provider("rpggeek")
class RPGGeekProvider(MetadataProvider):
    """RPGGeek catalogue provider (tabletop RPG manuals). Requires API token."""

    name = "rpggeek"
    display_name = "RPGGeek"
    requires_auth = True
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (SortOrder.RELEVANCE,)
    search_fields: ClassVar[tuple[Any, ...]] = (
        TextSearchField(
            key="title",
            label="Title",
            description="Search by manual title (Italian aliases accepted)",
        ),
    )

    def __init__(self, api_token: str | None = None) -> None:
        """Initialize provider."""
        self.api_token = _get_token(api_token)

    def is_available(self) -> bool:
        """RPGGeek is available once an API token is configured."""
        return bool(self.api_token)

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search the RPG catalogue."""
        if not self.api_token:
            return []
        if options.search_type == SearchType.ISBN:
            return []
        query = options.query.strip()
        fields_title = options.fields.get("title", "").strip()
        if fields_title:
            query = fields_title
        if not query:
            return []
        # The catalogue is English: translate known Italian titles.
        query = apply_alias(normalize_title(query)) or query
        items = search_items(query, self.api_token, limit=options.limit or MAX_CANDIDATES)
        books = [_item_to_book(item) for item in items]
        logger.info("RPGGeek search '%s' returned %s results", options.query, len(books))
        return books

    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get catalogue details by RPGGeek item ID."""
        if not self.api_token:
            return None
        item = fetch_item(book_id, self.api_token)
        return _item_to_book(item) if item is not None else None

    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """RPG items are not indexed by ISBN in the BGG API."""
        return None


def _test_rpggeek_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Action-button callback: verify the token against the BGG API."""
    raw_key = ""
    if current_values:
        raw_key = current_values.get("RPGEEK_API_TOKEN") or ""
    token = _get_token(str(raw_key) if raw_key else None)
    if not token:
        return {
            "success": False,
            "message": "Set an API token first (register your application at "
            "boardgamegeek.com/applications).",
        }
    try:
        items = search_items("dungeons and dragons", token, limit=3)
    except Exception as exc:
        logger.exception("RPGGeek connection test failed")
        return {"success": False, "message": f"Connection failed: {exc}"}
    if not items:
        return {
            "success": False,
            "message": "No answer from RPGGeek. The token may be invalid or expired.",
        }
    return {
        "success": True,
        "message": f"Connected to RPGGeek (e.g. '{items[0].name}').",
    }


@register_settings("rpggeek", "RPGGeek", icon="book", order=55, group="metadata_providers")
def rpggeek_settings() -> list[SettingsField]:
    """RPGGeek metadata provider settings."""
    return [
        HeadingField(
            key="rpggeek_heading",
            title="RPGGeek",
            description=(
                "The reference catalogue for tabletop role-playing games and their "
                "manuals. Used for manual cover lookup; can also be selected as a "
                "metadata provider."
            ),
            link_url="https://rpggeek.com",
            link_text="rpggeek.com",
        ),
        CheckboxField(
            key="RPGEEK_ENABLED",
            label="Enable RPGGeek",
            description="Enable RPGGeek as a metadata provider",
            default=False,
        ),
        PasswordField(
            key="RPGEEK_API_TOKEN",
            label="API Token",
            description=(
                "Bearer token for the BoardGameGeek/RPGGeek XML API. Register your "
                "application at boardgamegeek.com/applications (approval may take "
                "a week or more). Also enables manual cover lookup."
            ),
            required=False,
            env_supported=True,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify your API token works",
            style="primary",
            callback=_test_rpggeek_connection,
        ),
    ]
