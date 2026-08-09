"""Telegram release source.

Searches a Telegram bot for ebook and audiobook releases via MTProto user client.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any, ClassVar

from shelfmark.api.websocket import ws_manager
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import (
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceActionButton,
    register_source,
)

from .cache import cache_results, get_cached_results
from .client import client_manager
from .parser import parse_bot_response

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

MIN_SEARCH_INTERVAL = 5.0
_last_search_time: float = 0


def _config_text(key: str) -> str:
    value = config.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _config_bool(key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _config_int(key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError:
                return default
    return default


def _emit_status(message: str, phase: str = "searching") -> None:
    ws_manager.broadcast_search_status(
        source="telegram",
        provider="",
        book_id="",
        message=message,
        phase=phase,
    )


def _enforce_rate_limit() -> None:
    global _last_search_time
    elapsed = time.time() - _last_search_time
    if elapsed < MIN_SEARCH_INTERVAL:
        wait_time = MIN_SEARCH_INTERVAL - elapsed
        logger.info("Telegram rate limiting: waiting %.1fs", wait_time)
        time.sleep(wait_time)
    _last_search_time = time.time()


def _query_identity(bot_username: str, query: str) -> str:
    return f"telegram:{bot_username.casefold()}:{query.strip().casefold()}"


@register_source("telegram")
class TelegramSource(ReleaseSource):
    name = "telegram"
    display_name = "Telegram"
    supported_content_types: ClassVar[list[str]] = ["audiobook"]
    can_be_default = False

    def is_available(self) -> bool:
        enabled = _config_bool("TELEGRAM_ENABLED", False)
        if not enabled:
            return False
        bot_username = _config_text("TELEGRAM_BOT_USERNAME")
        return bool(bot_username) and client_manager.is_connected

    def get_column_config(self) -> ReleaseColumnConfig:
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    width="70px",
                    uppercase=True,
                    sortable=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    width="80px",
                    sortable=True,
                    sort_key="size_bytes",
                ),
                ColumnSchema(
                    key="extra.duration",
                    label="Duration",
                    render_type=ColumnRenderType.TEXT,
                    width="80px",
                    sortable=True,
                ),
            ],
            grid_template="minmax(0,2fr) 70px 80px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            cache_ttl_seconds=1800,
            supported_filters=["format"],
            action_button=SourceActionButton(label="Refresh search"),
        )

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        if not self.is_available():
            logger.debug("Telegram source is not available, skipping search")
            return []

        # Telegram fornisce solo audiolibri
        if content_type != "audiobook":
            logger.debug("Telegram source only supports audiobooks, skipping %s search", content_type)
            return []

        query = plan.primary_query or self._build_query(book)
        if not query:
            logger.warning("No search query could be built for Telegram")
            return []

        bot_username = _config_text("TELEGRAM_BOT_USERNAME")
        query_key = _query_identity(bot_username, query)

        if not expand_search:
            cached = get_cached_results(query_key)
            if cached:
                _emit_status("Using cached results", phase="complete")
                return self._filter_by_content_type(cached["releases"], content_type)

        _enforce_rate_limit()

        try:
            _emit_status("Preparing Telegram search...", phase="connecting")

            bot_entity = client_manager.resolve_bot_entity(bot_username)
            if bot_entity is None:
                _emit_status(f"Bot not found: {bot_username}", phase="error")
                logger.warning("Could not resolve Telegram bot: %s", bot_username)
                return []

            _emit_status(f"Sending query to {bot_username}...", phase="searching")

            search_command = _config_text("TELEGRAM_SEARCH_COMMAND")
            message_text = search_command.replace("{query}", query) if search_command else query

            sent_message = client_manager.send_message(bot_entity, message_text)
            if sent_message is None:
                _emit_status("Failed to send search query", phase="error")
                logger.error("Failed to send message to bot")
                return []
            
            logger.info("Message sent to bot, ID: %s, waiting for response...", sent_message.id)

            response_timeout = _config_int("TELEGRAM_RESPONSE_TIMEOUT", 60)
            _emit_status("Waiting for bot response...", phase="searching")

            bot_response = client_manager.wait_for_response(
                bot_entity,
                timeout=float(response_timeout),
                sent_message=sent_message,
            )

            logger.info("Bot response received: %s messages", len(bot_response.messages))

            if not bot_response.messages:
                _emit_status("No response from bot", phase="complete")
                logger.warning("Bot did not respond within %s seconds", response_timeout)
                cache_results(query_key, query, [])
                return []

            parsed_results = parse_bot_response(bot_response)
            logger.info("Parsed %s results from bot response", len(parsed_results))

            if not parsed_results and bot_response.callback_buttons:
                _emit_status("Bot returned buttons, clicking...", phase="searching")
                callback_result = client_manager.click_callback(
                    bot_entity,
                    bot_response.callback_buttons[0],
                )
                if callback_result is not None:
                    follow_up = client_manager.wait_for_callback_response(
                        bot_entity,
                        timeout=float(response_timeout),
                    )
                    if follow_up.messages:
                        parsed_results = parse_bot_response(follow_up)

            releases = self._convert_to_releases(parsed_results, content_type)

            # Filter results based on requested content_type
            releases = self._filter_by_content_type(releases, content_type)

            cache_results(query_key, query, releases)

            _emit_status(f"Found {len(releases)} results", phase="complete")
        except Exception:
            logger.exception("Telegram search failed")
            _emit_status("Search failed", phase="error")
            return []
        else:
            return releases

    def _build_query(self, book: BookMetadata) -> str:
        parts = []
        if book.search_title or book.title:
            parts.append(book.search_title or book.title)
        if book.search_author:
            parts.append(book.search_author)
        elif book.authors:
            author = book.authors[0] if isinstance(book.authors, list) else book.authors
            parts.append(author)
        return " ".join(parts)

    def _convert_to_releases(
        self,
        parsed_results: list,
        content_type: str = "ebook",
    ) -> list[Release]:
        releases = []

        for result in parsed_results:
            source_id = self._build_source_id(result)

            release = Release(
                source="telegram",
                source_id=source_id,
                title=result.title,
                format=result.format,
                size=result.size,
                size_bytes=result.size_bytes,
                protocol=ReleaseProtocol.TELEGRAM,
                indexer="Telegram",
                content_type=result.content_type or content_type,
                extra={
                    "message_id": result.message_id,
                    "chat_id": result.chat_id,
                    "document_id": result.document_id,
                    "has_document": result.has_document,
                    "file_name": result.file_name,
                    "duration": result.duration,
                    "author": result.author,
                    "narrator": result.narrator,
                    "callback_data": result.callback_data,
                },
            )
            releases.append(release)

        return releases

    @staticmethod
    def _build_source_id(result: object) -> str:
        msg_id = getattr(result, "message_id", None)
        chat_id = getattr(result, "chat_id", None)
        doc_id = getattr(result, "document_id", None)

        if msg_id and chat_id:
            raw = f"tg:{chat_id}:{msg_id}"
            if doc_id:
                raw += f":{doc_id}"
            return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

        title = getattr(result, "title", "unknown")
        return hashlib.md5(f"tg:{title}".encode(), usedforsecurity=False).hexdigest()

    def download_via_callback(
        self,
        callback_data: str,
        message_id: int,
        chat_id: int,
        timeout: float = 60.0,
    ) -> dict[str, Any] | None:
        """Click a callback button and wait for the document.
        
        Returns dict with document info (message_id, chat_id, document_id, file_name, size_bytes)
        or None if failed.
        """
        if not self.is_available():
            logger.warning("Telegram source not available for download")
            return None
        
        bot_username = _config_text("TELEGRAM_BOT_USERNAME")
        
        try:
            bot_entity = client_manager.resolve_bot_entity(bot_username)
            if bot_entity is None:
                logger.warning("Could not resolve bot entity: %s", bot_username)
                return None
            
            # Get the message with the inline keyboard
            message = client_manager.get_message(chat_id, message_id)
            if message is None:
                logger.warning("Could not get message %s/%s", chat_id, message_id)
                return None
            
            # Find the button with matching callback data
            target_button = None
            if message.reply_markup:
                for row in message.reply_markup.rows:
                    for btn in row.buttons:
                        btn_data = btn.data
                        if isinstance(btn_data, bytes):
                            btn_data = btn_data.decode("utf-8", errors="replace")
                        if btn_data == callback_data:
                            target_button = btn
                            break
                    if target_button:
                        break
            
            if target_button is None:
                logger.warning("Could not find button with callback data: %s", callback_data)
                return None
            
            # Click the button directly using message.click()
            logger.info("Clicking callback button: %s", callback_data)
            try:
                click_result = client_manager.click_message_button(message, target_button.data)
                logger.info("Click result: %s", click_result)
            except Exception as e:
                logger.exception("Failed to click button")
                return None
            
            # Wait for the document message
            logger.info("Waiting for document from bot...")
            response = client_manager.wait_for_document(
                bot_entity,
                timeout=timeout,
                after_message_id=message_id,
            )
            
            if not response.messages:
                logger.warning("No document received from bot")
                return None
            
            # Find the document in the response
            for msg in response.messages:
                if msg.document:
                    doc_info = {}
                    doc_info["message_id"] = msg.id
                    doc_info["chat_id"] = chat_id
                    doc_info["document_id"] = str(msg.document.id)
                    doc_info["size_bytes"] = getattr(msg.document, "size", None)
                    
                    # Extract filename
                    for attr in msg.document.attributes:
                        if hasattr(attr, "file_name"):
                            doc_info["file_name"] = attr.file_name
                            break
                    
                    logger.info("Document received: %s", doc_info.get("file_name"))
                    return doc_info
            
            logger.warning("No document found in bot response")
            return None
            
        except Exception:
            logger.exception("Failed to download via callback")
            return None

    @staticmethod
    def _filter_by_content_type(releases: list[Release], requested: str) -> list[Release]:
        return [r for r in releases if (r.content_type or "ebook") == requested]
