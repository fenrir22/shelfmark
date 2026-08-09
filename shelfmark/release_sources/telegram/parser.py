"""Telegram bot response parser.

Converts Telegram bot messages into Shelfmark Release objects.
The parser is configurable to handle different bot response formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from telethon.tl.custom.message import Message

    from .client import TelegramBotResponse

logger = setup_logger(__name__)

KNOWN_AUDIO_FORMATS = {"m4b", "mp3", "m4a", "flac", "opus", "ogg", "aac", "wav", "wma"}
KNOWN_EBOOK_FORMATS = {"epub", "mobi", "azw3", "azw", "pdf", "djvu", "fb2", "cbz", "cbr", "docx", "doc"}
KNOWN_ARCHIVE_FORMATS = {"rar", "zip", "7z", "tar", "gz"}
ALL_KNOWN_FORMATS = KNOWN_AUDIO_FORMATS | KNOWN_EBOOK_FORMATS | KNOWN_ARCHIVE_FORMATS

_FORMAT_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(f) for f in sorted(ALL_KNOWN_FORMATS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_SIZE_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(GB|MB|KB|B|GiB|MiB|KiB)",
    re.IGNORECASE,
)

_DURATION_PATTERN = re.compile(
    r"(?:(\d+)\s*h(?:ours?)?\s*)?(?:(\d+)\s*m(?:in(?:utes?)?)?)"
    r"(?:\s*(\d+)\s*s(?:ec(?:onds?)?)?)?",
)

_TITLE_AUTHOR_PATTERN = re.compile(
    r"^(.+?)(?:\s*[-–—]\s*(.+))?$",
    re.MULTILINE,
)


@dataclass
class TelegramParsedResult:
    """A single parsed result from a Telegram bot response."""

    title: str
    author: str | None = None
    narrator: str | None = None
    description: str | None = None
    format: str | None = None
    size: str | None = None
    size_bytes: int | None = None
    duration: str | None = None
    language: str | None = None
    cover_url: str | None = None
    message_id: int | None = None
    chat_id: int | None = None
    has_document: bool = False
    document_id: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    callback_data: str | None = None


def _extract_format(text: str) -> str | None:
    match = _FORMAT_PATTERN.search(text)
    if match:
        return match.group(1).lower()
    return None


def _extract_size(text: str) -> tuple[str | None, int | None]:
    match = _SIZE_PATTERN.search(text)
    if not match:
        return None, None

    num_str = match.group(1).replace(",", ".")
    unit = match.group(2).upper()

    try:
        num = float(num_str)
    except ValueError:
        return None, None

    unit_multipliers = {
        "B": 1,
        "KB": 1024,
        "KIB": 1024,
        "MB": 1024 * 1024,
        "MIB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "GIB": 1024 * 1024 * 1024,
    }

    size_bytes = int(num * unit_multipliers.get(unit, 1))
    display = match.group(0)
    return display, size_bytes


def _extract_duration(text: str) -> str | None:
    match = _DURATION_PATTERN.search(text)
    if not match:
        return None

    hours = match.group(1)
    minutes = match.group(2)
    seconds = match.group(3)

    if not hours and not minutes and not seconds:
        return None

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not hours:
        parts.append(f"{seconds}s")

    return " ".join(parts) if parts else None


def _extract_title_author(text: str) -> tuple[str, str | None]:
    lines = text.strip().split("\n")
    first_line = lines[0].strip() if lines else text.strip()

    match = _TITLE_AUTHOR_PATTERN.match(first_line)
    if match:
        title = match.group(1).strip()
        author = match.group(2)
        if author:
            author = author.strip()
        return title, author

    return first_line, None


def _guess_content_type(fmt: str | None, file_name: str | None = None) -> str | None:
    if not fmt:
        return None
    fmt_lower = fmt.lower()
    if fmt_lower in KNOWN_AUDIO_FORMATS:
        return "audiobook"
    if fmt_lower in KNOWN_EBOOK_FORMATS:
        return "ebook"
    if file_name:
        file_lower = file_name.lower()
        if any(audio_fmt in file_lower for audio_fmt in KNOWN_AUDIO_FORMATS):
            return "audiobook"
        if any(ebook_fmt in file_lower for ebook_fmt in KNOWN_EBOOK_FORMATS):
            return "ebook"
    return None


def _extract_document_info(message: Message) -> dict[str, Any]:
    info: dict[str, Any] = {}
    doc = message.document
    if doc is None:
        return info

    info["document_id"] = str(doc.id)
    info["has_document"] = True

    for attr in doc.attributes:
        file_name = getattr(attr, "file_name", None)
        if file_name:
            info["file_name"] = file_name
            if not info.get("format"):
                ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None
                if ext and ext in ALL_KNOWN_FORMATS:
                    info["format"] = ext
        duration = getattr(attr, "duration", None)
        if duration:
            minutes = duration // 60
            hours = minutes // 60
            remaining_minutes = minutes % 60
            if hours > 0:
                info["duration"] = f"{hours}h {remaining_minutes}m"
            elif remaining_minutes > 0:
                info["duration"] = f"{remaining_minutes}m"

    info["size_bytes"] = getattr(doc, "size", None)

    return info


def parse_bot_response(response: TelegramBotResponse) -> list[TelegramParsedResult]:
    """Parse a bot response into a list of results.
    
    Supports multiple bot formats:
    - Book-list: 📚 `ID`\nAuthor Title with dl:ID buttons
    - Generic: Text-based results with metadata
    """
    if not response.messages:
        return []

    results: list[TelegramParsedResult] = []

    for message in response.messages:
        text = message.text or message.message or ""
        
        # Check if this is a book-list style response
        if "📚" in text and "`" in text:
            parsed = _parse_booklist_response(message, response)
            results.extend(parsed)
            continue
        
        chat_id = None
        if hasattr(message, "chat_id"):
            chat_id = message.chat_id
        elif hasattr(message, "peer_id") and message.peer_id:
            peer = message.peer_id
            chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)

        doc_info = _extract_document_info(message)

        if doc_info.get("has_document"):
            file_name = doc_info.get("file_name", "")
            title = file_name.rsplit(".", 1)[0] if file_name and "." in file_name else file_name or text[:100]
            author = None
            fmt = doc_info.get("format") or _extract_format(file_name) or _extract_format(text)

            result = TelegramParsedResult(
                title=title,
                author=author,
                format=fmt,
                size_bytes=doc_info.get("size_bytes"),
                duration=doc_info.get("duration"),
                message_id=message.id,
                chat_id=chat_id,
                has_document=True,
                document_id=doc_info.get("document_id"),
                file_name=file_name,
                content_type=_guess_content_type(fmt, file_name),
            )
            results.append(result)
            continue

        if text.strip():
            title, author = _extract_title_author(text)
            fmt = _extract_format(text)
            size_display, size_bytes = _extract_size(text)
            duration = _extract_duration(text)

            callback_data = None
            if response.callback_buttons:
                callback_data = response.callback_buttons[0].data
                if isinstance(callback_data, bytes):
                    callback_data = callback_data.decode("utf-8", errors="replace")

            result = TelegramParsedResult(
                title=title,
                author=author,
                format=fmt,
                size=size_display,
                size_bytes=size_bytes,
                duration=duration,
                message_id=message.id,
                chat_id=chat_id,
                has_document=False,
                content_type=_guess_content_type(fmt),
                callback_data=callback_data,
            )
            results.append(result)

    return results


def _parse_booklist_response(message: Any, response: TelegramBotResponse) -> list[TelegramParsedResult]:
    """Parse book-list format: 📚 `ID`\nAuthor Title"""
    import re
    
    text = message.text or ""
    results = []
    
    # Extract all book entries: 📚 `ID`\nAuthor Title
    pattern = r'📚 `(\d+)`\n([^\n]+)'
    matches = re.findall(pattern, text)
    
    chat_id = None
    if hasattr(message, "chat_id"):
        chat_id = message.chat_id
    elif hasattr(message, "peer_id") and message.peer_id:
        peer = message.peer_id
        chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)
    
    # Build a map of callback buttons by book ID
    button_map = {}
    if response.callback_buttons:
        for btn in response.callback_buttons:
            data = btn.data
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            if data.startswith("dl:"):
                book_id = data.replace("dl:", "")
                button_map[book_id] = data
    
    for book_id, metadata_line in matches:
        # Parse metadata line: "Platform Author Title" or "Author Title"
        clean_line = metadata_line.strip()
        
        # Remove platform prefix if present
        platforms = ["Storytel", "Audible", "Google", "Apple", "Spotify"]
        for platform in platforms:
            if clean_line.startswith(platform + " "):
                clean_line = clean_line[len(platform):].strip()
                break
        
        # Split author and title
        if " - " in clean_line:
            parts = clean_line.split(" - ", 1)
            author = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else clean_line
        else:
            author = None
            title = clean_line
        
        # Get callback data for this book
        callback_data = button_map.get(book_id)
        
        result = TelegramParsedResult(
            title=title,
            author=author,
            narrator=None,
            description=None,
            format=None,
            size=None,
            size_bytes=None,
            duration=None,
            language=None,
            cover_url=None,
            message_id=message.id,
            chat_id=chat_id,
            has_document=False,
            document_id=None,
            file_name=None,
            content_type="audiobook",
            callback_data=callback_data,
        )
        results.append(result)
    
    return results


def parse_single_result_from_text(text: str, message: Message | None = None) -> TelegramParsedResult | None:
    if not text or not text.strip():
        return None

    title, author = _extract_title_author(text)
    fmt = _extract_format(text)
    size_display, size_bytes = _extract_size(text)
    duration = _extract_duration(text)

    chat_id = None
    message_id = None
    if message is not None:
        message_id = message.id
        if hasattr(message, "chat_id"):
            chat_id = message.chat_id
        elif hasattr(message, "peer_id") and message.peer_id:
            peer = message.peer_id
            chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)

    return TelegramParsedResult(
        title=title,
        author=author,
        format=fmt,
        size=size_display,
        size_bytes=size_bytes,
        duration=duration,
        message_id=message_id,
        chat_id=chat_id,
        content_type=_guess_content_type(fmt),
    )
