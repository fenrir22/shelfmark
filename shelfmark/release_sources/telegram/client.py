"""Telegram MTProto client manager.

Manages a persistent Telethon client session for communicating with Telegram bots.
Runs the Telethon event loop in a dedicated thread and provides synchronous wrappers.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from telethon.tl.custom.message import Message
    from telethon.tl.types import (
        KeyboardButtonCallback,
    )

logger = setup_logger(__name__)

TELEGRAM_CONNECT_TIMEOUT = 30
TELEGRAM_DEFAULT_RESPONSE_TIMEOUT = 60
TELEGRAM_MAX_RESPONSE_TIMEOUT = 300
TELEGRAM_RECONNECT_DELAY = 5.0
# Downloads wait as long as progress is being made; abort only after this many
# seconds without any progress (protects against network stalls without killing
# slow-but-active transfers of multi-gigabyte files).
TELEGRAM_DOWNLOAD_IDLE_TIMEOUT = 300


@dataclass
class TelegramAuthState:
    """Tracks the current authentication flow state."""

    phone: str = ""
    phone_code_hash: str = ""
    is_waiting_code: bool = False
    is_waiting_2fa: bool = False
    error: str = ""


@dataclass
class TelegramBotResponse:
    """Parsed response from a Telegram bot."""

    messages: list[Message] = field(default_factory=list)
    callback_buttons: list[KeyboardButtonCallback] = field(default_factory=list)
    raw_text: str = ""

    @property
    def has_documents(self) -> bool:
        return any(m.document for m in self.messages)

    @property
    def first_document(self) -> Any | None:
        for m in self.messages:
            if m.document:
                return m.document
        return None

    @property
    def first_document_message(self) -> Message | None:
        for m in self.messages:
            if m.document:
                return m
        return None


class TelegramClientManager:
    """Manages a persistent Telegram MTProto client.

    Runs the Telethon event loop in a dedicated daemon thread and provides
    synchronous wrappers for all Telegram operations.
    """

    _instance: TelegramClientManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> Self:  # type: ignore[reportReturnType]
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance  # type: ignore[reportReturnType]

    def __init__(self) -> None:
        if self._initialized:
            return

        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._auth_state = TelegramAuthState()
        self._session_path: Path | None = None
        self._initialized = True
        self._status: str = "disconnected"
        self._username: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    @property
    def status(self) -> str:
        return self._status

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def auth_state(self) -> TelegramAuthState:
        return self._auth_state

    def _start_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and self._loop.is_running():
            return self._loop

        self._thread = threading.Thread(target=self._start_loop, daemon=True, name="telegram-mtproto")
        self._thread.start()

        deadline = time.monotonic() + 10
        while self._loop is None or not self._loop.is_running():
            if time.monotonic() > deadline:
                msg = "Telegram event loop failed to start"
                raise RuntimeError(msg)
            time.sleep(0.05)

        return self._loop

    def _run_sync(self, coro: Any, timeout: float = TELEGRAM_CONNECT_TIMEOUT) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    def _create_client(self, api_id: int, api_hash: str, session_path: str) -> Any:
        from telethon import TelegramClient

        return TelegramClient(session_path, api_id, api_hash)

    async def _connect_async(self, api_id: int, api_hash: str, session_path: str) -> bool:
        from telethon.errors import (
            AuthKeyUnregisteredError,
            SessionPasswordNeededError,
        )

        try:
            if self._client is not None:
                with contextlib.suppress(Exception):
                    await self._client.disconnect()
                self._client = None

            self._session_path = Path(session_path)
            self._session_path.parent.mkdir(parents=True, exist_ok=True)

            self._client = self._create_client(api_id, api_hash, session_path)
            await self._client.connect()

            if await self._client.is_user_authorized():
                me = await self._client.get_me()
                self._username = getattr(me, "username", None) or str(getattr(me, "id", ""))
                self._connected = True
                self._status = "connected"
                logger.info("Telegram client connected as @%s", self._username)
                return True

            self._status = "auth_required"
            logger.info("Telegram session requires authentication")
        except (AuthKeyUnregisteredError, SessionPasswordNeededError):
            self._status = "auth_required"
            return False
        except Exception:
            logger.exception("Failed to connect Telegram client")
            self._status = "error"
            self._connected = False
            return False
        else:
            return False

    def connect(self, api_id: int, api_hash: str, session_path: str) -> bool:
        try:
            return self._run_sync(self._connect_async(api_id, api_hash, session_path))
        except Exception:
            logger.exception("Telegram connect failed")
            self._status = "error"
            return False

    async def _send_code_async(self, phone: str) -> dict[str, Any]:
        try:
            result = await self._client.send_code_request(phone)
        except Exception as e:
            self._auth_state.error = str(e)
            logger.exception("Failed to send code")
            return {"success": False, "message": f"Failed to send code: {e}"}
        else:
            self._auth_state.phone = phone
            self._auth_state.phone_code_hash = result.phone_code_hash
            self._auth_state.is_waiting_code = True
            self._auth_state.is_waiting_2fa = False
            self._auth_state.error = ""
            return {"success": True, "message": "Code sent to your Telegram app"}

    def send_code(self, phone: str) -> dict[str, Any]:
        if not self._client:
            return {"success": False, "message": "Client not initialized"}
        try:
            return self._run_sync(self._send_code_async(phone))
        except Exception as e:
            logger.exception("send_code sync wrapper failed")
            return {"success": False, "message": f"Failed to send code: {e}"}

    async def _sign_in_async(self, code: str) -> dict[str, Any]:
        from telethon.errors import SessionPasswordNeededError

        try:
            await self._client.sign_in(
                phone=self._auth_state.phone,
                code=code,
                phone_code_hash=self._auth_state.phone_code_hash,
            )
        except SessionPasswordNeededError:
            self._auth_state.is_waiting_code = False
            self._auth_state.is_waiting_2fa = True
            return {"success": False, "message": "2FA password required", "needs_2fa": True}
        except Exception as e:
            self._auth_state.error = str(e)
            logger.exception("Sign in failed")
            return {"success": False, "message": f"Sign in failed: {e}"}
        else:
            me = await self._client.get_me()
            self._username = getattr(me, "username", None) or str(getattr(me, "id", ""))
            self._connected = True
            self._status = "connected"
            self._auth_state.is_waiting_code = False
            self._auth_state.is_waiting_2fa = False
            logger.info("Telegram authenticated as @%s", self._username)
            return {"success": True, "message": f"Connected as @{self._username}"}

    def sign_in(self, code: str) -> dict[str, Any]:
        if not self._client:
            return {"success": False, "message": "Client not initialized"}
        try:
            return self._run_sync(self._sign_in_async(code))
        except Exception as e:
            logger.exception("sign_in sync wrapper failed")
            return {"success": False, "message": f"Sign in failed: {e}"}

    async def _sign_in_2fa_async(self, password: str) -> dict[str, Any]:
        try:
            await self._client.sign_in(password=password)
        except Exception as e:
            self._auth_state.error = str(e)
            logger.exception("2FA sign in failed")
            return {"success": False, "message": f"2FA failed: {e}"}
        else:
            me = await self._client.get_me()
            self._username = getattr(me, "username", None) or str(getattr(me, "id", ""))
            self._connected = True
            self._status = "connected"
            self._auth_state.is_waiting_2fa = False
            logger.info("Telegram authenticated (2FA) as @%s", self._username)
            return {"success": True, "message": f"Connected as @{self._username}"}

    def sign_in_2fa(self, password: str) -> dict[str, Any]:
        if not self._client:
            return {"success": False, "message": "Client not initialized"}
        try:
            return self._run_sync(self._sign_in_2fa_async(password))
        except Exception as e:
            logger.exception("sign_in_2fa sync wrapper failed")
            return {"success": False, "message": f"2FA failed: {e}"}

    async def _disconnect_async(self) -> None:
        try:
            if self._client is not None:
                await self._client.disconnect()
        except Exception:
            logger.debug("Error during Telegram async disconnect", exc_info=True)
        finally:
            self._client = None
            self._connected = False
            self._status = "disconnected"
            self._username = None
            self._auth_state = TelegramAuthState()

    def disconnect(self) -> None:
        try:
            self._run_sync(self._disconnect_async(), timeout=10)
        except Exception:
            logger.exception("Error during Telegram disconnect")
            self._client = None
            self._connected = False
            self._status = "disconnected"

    async def _resolve_bot_entity_async(self, bot_username: str) -> Any:
        entity_ref: Any = bot_username
        if isinstance(bot_username, str):
            stripped = bot_username.strip()
            if stripped.startswith("https://t.me/c/"):
                # Private chat link: t.me/c/<chat_id>[/<message_id>]
                import re

                match = re.match(r"https://t\.me/c/(\d+)(?:/(\d+))?", stripped)
                if match:
                    entity_ref = int(f"-100{match.group(1)}")
            elif stripped.lstrip("-").isdigit():
                entity_ref = int(stripped)
        return await self._client.get_entity(entity_ref)

    def resolve_bot_entity(self, bot_username: str) -> Any:
        if not self.is_connected:
            return None
        try:
            return self._run_sync(self._resolve_bot_entity_async(bot_username))
        except Exception:
            logger.exception("Failed to resolve bot entity: %s", bot_username)
            return None

    async def _send_message_async(self, entity: Any, text: str) -> Any:
        return await self._client.send_message(entity, text)

    def send_message(self, entity: Any, text: str) -> Any:
        if not self.is_connected:
            return None
        try:
            return self._run_sync(self._send_message_async(entity, text))
        except Exception:
            logger.exception("Failed to send message to bot")
            return None

    async def _get_message_async(self, chat_id: int, message_id: int) -> Any:
        return await self._client.get_messages(chat_id, ids=message_id)

    def get_message(self, chat_id: int, message_id: int) -> Any:
        """Get a specific message by chat_id and message_id."""
        if not self.is_connected:
            return None
        try:
            return self._run_sync(self._get_message_async(chat_id, message_id))
        except Exception:
            logger.exception("Failed to get message %s/%s", chat_id, message_id)
            return None

    async def _search_messages_async(
        self,
        entity: Any,
        query: str,
        limit: int = 50,
        reply_to: int | None = None,
        add_offset: int = 0,
    ) -> list:
        # Only search file/media messages (documents), never chat discussion.
        from telethon.tl.types import InputMessagesFilterDocument

        document_filter = InputMessagesFilterDocument()
        if reply_to is not None:
            # Telethon ignores the server-side full-text search when scoping to a
            # forum topic (the topic filter overrides it). Fetch the topic's
            # documents and filter locally against message text and file names.
            results = []
            current_offset = 0
            fetch_limit = max(limit, 100)
            while len(results) < max(limit, 100):
                batch = await self._client.get_messages(
                    entity,
                    reply_to=reply_to,
                    filter=document_filter,
                    limit=fetch_limit,
                    offset_id=current_offset,
                )
                if not batch:
                    break
                results.extend(batch)
                if len(batch) < fetch_limit:
                    break
                current_offset = batch[-1].id
            return self._filter_messages_local(results, query, limit)
        if add_offset > 0:
            from telethon.tl.functions.messages import SearchRequest
            from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser
            from telethon import utils

            peer = utils.get_input_peer(entity)
            request = SearchRequest(
                peer=peer,
                q=query,
                filter=document_filter,
                min_date=None,
                max_date=None,
                offset_id=0,
                add_offset=add_offset,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0,
            )
            result = await self._client(request)
            return result.messages
        return await self._client.get_messages(
            entity,
            search=query,
            filter=document_filter,
            limit=limit,
        )

    @staticmethod
    def _message_matches_query(message: Any, query: str) -> bool:
        if not query:
            return True
        needle = query.strip().lower()
        text = (message.text or "").lower()
        if needle in text:
            return True
        document = getattr(message, "document", None)
        if document is None:
            return False
        for attr in getattr(document, "attributes", []):
            file_name = getattr(attr, "file_name", None)
            if file_name and needle in file_name.lower():
                return True
        return False

    def _filter_messages_local(self, messages: list, query: str, limit: int) -> list:
        matched = [
            m for m in messages if self._message_matches_query(m, query)
        ]
        return matched[:limit]

    def search_messages(
        self,
        entity: Any,
        query: str,
        limit: int = 50,
        reply_to: int | None = None,
        add_offset: int = 0,
    ) -> list:
        """Search a chat's message history without sending any message.

        Uses Telegram server-side full-text search, which covers message text
        and document file names. Only reads existing history. When ``reply_to``
        is a forum topic root message id, the search is limited to that topic.
        """
        if not self.is_connected:
            return []
        try:
            return self._run_sync(
                self._search_messages_async(entity, query, limit, reply_to, add_offset),
                timeout=TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
            )
        except Exception:
            logger.exception("Failed to search messages with query: %s", query)
            return []

    async def _find_forum_topic_async(self, entity: Any, topic_title: str) -> int | None:
        """Find a forum topic's root message id by its title."""
        from telethon.tl.functions.messages import GetForumTopicsRequest

        query = topic_title.strip().lower()
        offset_date = None
        offset_id = 0
        offset_topic = 0
        for _ in range(20):
            result = await self._client(
                GetForumTopicsRequest(
                    peer=entity,
                    q=topic_title.strip(),
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=100,
                )
            )
            for topic in result.topics:
                action = getattr(topic, "action", None)
                title = (getattr(action, "title", "") or "").strip()
                if title.lower() == query:
                    return getattr(topic, "id", None)
            if len(result.topics) < 100:
                break
            last = result.topics[-1]
            offset_id = getattr(last, "id", 0)
            offset_topic = offset_id
            offset_date = getattr(last, "date", None)
        return None

    def find_forum_topic(self, entity: Any, topic_title: str) -> int | None:
        """Find a forum topic's root message id by its title."""
        if not self.is_connected:
            return None
        try:
            return self._run_sync(
                self._find_forum_topic_async(entity, topic_title),
                timeout=TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
            )
        except Exception:
            logger.exception("Failed to find forum topic: %s", topic_title)
            return None

    async def _resolve_dialog_by_title_async(self, title: str) -> Any | None:
        wanted = title.strip().lower()
        async for dialog in self._client.iter_dialogs():
            if (dialog.title or "").strip().lower() == wanted:
                return dialog.entity
        return None

    def resolve_dialog_by_title(self, title: str) -> Any | None:
        """Resolve a chat/channel/group by its display title.

        Useful when only the group's display name is known (no username).
        """
        if not self.is_connected:
            return None
        try:
            return self._run_sync(
                self._resolve_dialog_by_title_async(title),
                timeout=TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
            )
        except Exception:
            logger.exception("Failed to resolve dialog by title: %s", title)
            return None

    async def _wait_for_response_async(
        self,
        entity: Any,
        timeout: float = TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
        sent_message: Any = None,
    ) -> TelegramBotResponse:
        import asyncio as _asyncio
        import time

        response = TelegramBotResponse()
        
        min_msg_id = 0
        if sent_message is not None:
            min_msg_id = getattr(sent_message, "id", 0)
            logger.debug("Waiting for response after message ID: %s", min_msg_id)
        
        # Poll for new messages
        start_time = time.time()
        poll_count = 0
        
        while time.time() - start_time < timeout:
            poll_count += 1
            messages = await self._client.get_messages(entity, limit=20)
            
            if poll_count % 10 == 0:
                logger.debug("Poll #%d: found %d messages", poll_count, len(messages))
            
            for msg in messages:
                # Skip our own messages
                if msg.out:
                    continue
                
                # Skip messages with ID <= our sent message ID
                if min_msg_id > 0 and msg.id <= min_msg_id:
                    continue
                
                logger.info("Found response message from bot: ID=%s, date=%s", msg.id, msg.date)
                
                # Found a response
                response.messages.append(msg)
                
                # Collect text
                if msg.text:
                    if response.raw_text:
                        response.raw_text += "\n"
                    response.raw_text += msg.text
                
                # Collect callback buttons
                if msg.reply_markup and hasattr(msg.reply_markup, "rows"):
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            from telethon.tl.types import KeyboardButtonCallback
                            if isinstance(btn, KeyboardButtonCallback):
                                response.callback_buttons.append(btn)
                
                # If we got a response, wait a bit more for additional messages
                await _asyncio.sleep(1.0)
                
                # Get any additional messages
                more_messages = await self._client.get_messages(entity, limit=20)
                for msg2 in more_messages:
                    if msg2.out or msg2.id == msg.id:
                        continue
                    if min_msg_id > 0 and msg2.id <= min_msg_id:
                        continue
                    if msg2 not in response.messages:
                        response.messages.append(msg2)
                        if msg2.text:
                            if response.raw_text:
                                response.raw_text += "\n"
                            response.raw_text += msg2.text
                
                return response
            
            # Use asyncio.sleep for async context
            await _asyncio.sleep(0.5)
        
        logger.warning("Polling timeout after %d polls (%.1fs), no response received", poll_count, time.time() - start_time)
        return response

    def wait_for_response(
        self,
        entity: Any,
        timeout: float = TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
        sent_message: Any = None,
    ) -> TelegramBotResponse:
        if not self.is_connected:
            return TelegramBotResponse()
        try:
            return self._run_sync(
                self._wait_for_response_async(entity, timeout, sent_message),
                timeout=timeout + 15,
            )
        except Exception:
            logger.exception("Failed waiting for bot response")
            return TelegramBotResponse()

    def click_message_button(self, message: Any, button_data: bytes | str) -> Any:
        """Click a button on a message using message.click()."""
        if not self.is_connected:
            return None
        try:
            return self._run_sync(self._click_message_button_async(message, button_data))
        except Exception:
            logger.exception("Failed to click message button")
            return None

    async def _click_message_button_async(self, message: Any, button_data: bytes | str) -> Any:
        """Click a button on a message using message.click()."""
        return await message.click(data=button_data)

    async def _wait_for_document_async(
        self,
        entity: Any,
        timeout: float = TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
        after_message_id: int = 0,
    ) -> TelegramBotResponse:
        """Wait for a message containing a document from the bot."""
        import asyncio as _asyncio
        import time

        response = TelegramBotResponse()
        
        # Poll for new messages with documents
        start_time = time.time()
        poll_count = 0
        
        while time.time() - start_time < timeout:
            poll_count += 1
            messages = await self._client.get_messages(entity, limit=20)
            
            if poll_count % 10 == 0:
                logger.debug("Document poll #%d: found %d messages", poll_count, len(messages))
            
            for msg in messages:
                # Skip our own messages
                if msg.out:
                    continue
                
                # Skip messages with ID <= after_message_id
                if after_message_id > 0 and msg.id <= after_message_id:
                    continue
                
                # Check if this message has a document
                if msg.document:
                    logger.info("Found document message: ID=%s, size=%s", msg.id, msg.document.size)
                    response.messages.append(msg)
                    return response
                
                # Also collect text messages (bot might send info before document)
                if msg.text:
                    response.messages.append(msg)
                    if response.raw_text:
                        response.raw_text += "\n"
                    response.raw_text += msg.text
            
            await _asyncio.sleep(0.5)
        
        logger.warning("Document polling timeout after %d polls (%.1fs)", poll_count, time.time() - start_time)
        return response

    def wait_for_document(
        self,
        entity: Any,
        timeout: float = TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
        after_message_id: int = 0,
    ) -> TelegramBotResponse:
        """Wait for a message containing a document from the bot."""
        if not self.is_connected:
            return TelegramBotResponse()
        try:
            return self._run_sync(
                self._wait_for_document_async(entity, timeout, after_message_id),
                timeout=timeout + 15,
            )
        except Exception:
            logger.exception("Failed waiting for document")
            return TelegramBotResponse()

    async def _click_callback_async(self, entity: Any, button: KeyboardButtonCallback) -> Any:
        msg_id = getattr(button, "_message_id", None)
        message = await self._client.get_messages(entity, ids=msg_id)
        if message is None:
            messages = await self._client.get_messages(entity, limit=5)
            for msg in messages:
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, "data") and btn.data == button.data:
                                message = msg
                                break
                        if message:
                            break
                if message:
                    break

        if message is None:
            logger.warning("Could not find message for callback button click")
            return None

        return await message.click(data=button.data)

    def click_callback(self, entity: Any, button: KeyboardButtonCallback) -> Any:
        if not self.is_connected:
            return None
        try:
            return self._run_sync(self._click_callback_async(entity, button))
        except Exception:
            logger.exception("Failed to click callback button")
            return None

    async def _wait_for_callback_response_async(
        self,
        entity: Any,
        timeout: float = TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
    ) -> TelegramBotResponse:
        import asyncio as _asyncio

        response = TelegramBotResponse()
        
        # Poll for new messages
        start_time = _asyncio.get_event_loop().time()
        while _asyncio.get_event_loop().time() - start_time < timeout:
            messages = await self._client.get_messages(entity, limit=20)
            
            for msg in messages:
                # Skip our own messages
                if msg.out:
                    continue
                
                # Found a response
                response.messages.append(msg)
                
                # Collect text
                if msg.text:
                    if response.raw_text:
                        response.raw_text += "\n"
                    response.raw_text += msg.text
                
                # Collect callback buttons
                if msg.reply_markup and hasattr(msg.reply_markup, "rows"):
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            from telethon.tl.types import KeyboardButtonCallback
                            if isinstance(btn, KeyboardButtonCallback):
                                response.callback_buttons.append(btn)
                
                # Wait a bit for additional messages
                await _asyncio.sleep(1.0)
                
                # Get any additional messages
                more_messages = await self._client.get_messages(entity, limit=20)
                for msg2 in more_messages:
                    if msg2.out or msg2.id == msg.id:
                        continue
                    if msg2 not in response.messages:
                        response.messages.append(msg2)
                        if msg2.text:
                            if response.raw_text:
                                response.raw_text += "\n"
                            response.raw_text += msg2.text
                
                return response
            
            await _asyncio.sleep(0.5)
        
        return response

    def wait_for_callback_response(
        self,
        entity: Any,
        timeout: float = TELEGRAM_DEFAULT_RESPONSE_TIMEOUT,
    ) -> TelegramBotResponse:
        if not self.is_connected:
            return TelegramBotResponse()
        try:
            return self._run_sync(
                self._wait_for_callback_response_async(entity, timeout),
                timeout=timeout + 15,
            )
        except Exception:
            logger.exception("Failed waiting for callback response")
            return TelegramBotResponse()

    async def _download_media_async(
        self,
        message: Any,
        output_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_flag: threading.Event | None = None,
    ) -> str | None:
        if cancel_flag is not None and cancel_flag.is_set():
            return None

        def _progress(current: int, total: int) -> None:
            if cancel_flag is not None and cancel_flag.is_set():
                raise KeyboardInterrupt
            if progress_callback:
                progress_callback(current, total)

        try:
            result = await self._client.download_media(
                message,
                file=output_path,
                progress_callback=_progress,
            )
            return str(result) if result else None
        except KeyboardInterrupt:
            logger.info("Telegram download cancelled")
            return None

    def download_media(
        self,
        message: Any,
        output_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_flag: threading.Event | None = None,
        idle_timeout: float = TELEGRAM_DOWNLOAD_IDLE_TIMEOUT,
    ) -> str | None:
        """Download media, waiting as long as progress is being made.

        Instead of a hard wall-clock deadline (which aborts slow downloads such
        as multi-gigabyte files mid-transfer), this waits indefinitely but
        aborts with ``None`` if no progress is reported for ``idle_timeout``
        seconds, or when ``cancel_flag`` is set.
        """
        if not self.is_connected:
            return None

        loop = self._ensure_loop()
        last_activity = time.monotonic()

        def _track_progress(current: int, total: int) -> None:
            nonlocal last_activity
            last_activity = time.monotonic()
            if progress_callback:
                progress_callback(current, total)

        future = asyncio.run_coroutine_threadsafe(
            self._download_media_async(message, output_path, _track_progress, cancel_flag),
            loop,
        )

        try:
            while True:
                try:
                    return future.result(timeout=2.0)
                except TimeoutError:
                    pass
                if cancel_flag is not None and cancel_flag.is_set():
                    # Let the async download observe the flag and unwind.
                    continue
                if time.monotonic() - last_activity > idle_timeout:
                    logger.warning(
                        "Telegram download stalled (no progress for %.0fs), aborting",
                        idle_timeout,
                    )
                    future.cancel()
                    return None
        except Exception:
            logger.exception("Failed to download media from Telegram")
            return None

    async def _test_connection_async(self) -> dict[str, Any]:
        try:
            me = await self._client.get_me()
        except Exception as e:
            logger.exception("Connection test failed")
            return {"success": False, "message": f"Connection test failed: {e}"}
        else:
            if me:
                self._username = getattr(me, "username", None) or str(getattr(me, "id", ""))
                self._connected = True
                self._status = "connected"
                return {"success": True, "message": f"Connected as @{self._username}"}
            return {"success": False, "message": "Not authorized"}

    def test_connection(self) -> dict[str, Any]:
        if not self._client:
            return {"success": False, "message": "Client not initialized"}
        try:
            return self._run_sync(self._test_connection_async())
        except Exception as e:
            logger.exception("test_connection sync wrapper failed")
            return {"success": False, "message": f"Connection test failed: {e}"}


client_manager = TelegramClientManager()
