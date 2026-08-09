from types import SimpleNamespace

from shelfmark.release_sources.telegram.parser import (
    TelegramParsedResult,
    _extract_duration,
    _extract_format,
    _extract_size,
    _extract_title_author,
    _guess_content_type,
    parse_bot_response,
    parse_single_result_from_text,
)
from shelfmark.release_sources.telegram.client import TelegramBotResponse


def test_extract_format_known_audio():
    assert _extract_format("Dune.m4b") == "m4b"
    assert _extract_format("audiobook mp3 file") == "mp3"
    assert _extract_format("FLAC lossless") == "flac"


def test_extract_format_known_ebook():
    assert _extract_format("Dune.epub") == "epub"
    assert _extract_format("PDF document") == "pdf"
    assert _extract_format("mobi file") == "mobi"


def test_extract_format_unknown():
    assert _extract_format("no format here") is None


def test_extract_size_megabytes():
    display, size_bytes = _extract_size("File size: 500MB")
    assert display == "500MB"
    assert size_bytes == 500 * 1024 * 1024


def test_extract_size_gigabytes():
    display, size_bytes = _extract_size("1.5GB")
    assert display == "1.5GB"
    assert size_bytes == int(1.5 * 1024 * 1024 * 1024)


def test_extract_size_kilobytes():
    display, size_bytes = _extract_size("256KB")
    assert display == "256KB"
    assert size_bytes == 256 * 1024


def test_extract_size_none():
    display, size_bytes = _extract_size("no size here")
    assert display is None
    assert size_bytes is None


def test_extract_duration_hours_minutes():
    assert _extract_duration("Duration: 21h 14m") == "21h 14m"


def test_extract_duration_minutes_only():
    assert _extract_duration("45m") == "45m"


def test_extract_duration_none():
    assert _extract_duration("no duration") is None


def test_extract_title_author_with_dash():
    title, author = _extract_title_author("Dune - Frank Herbert")
    assert title == "Dune"
    assert author == "Frank Herbert"


def test_extract_title_author_without_author():
    title, author = _extract_title_author("Dune")
    assert title == "Dune"
    assert author is None


def test_extract_title_author_multiline():
    title, author = _extract_title_author("Dune\nFrank Herbert\n21h 14m")
    assert title == "Dune"
    assert author is None


def test_guess_content_type_audio_formats():
    assert _guess_content_type("m4b") == "audiobook"
    assert _guess_content_type("mp3") == "audiobook"
    assert _guess_content_type("flac") == "audiobook"


def test_guess_content_type_ebook_formats():
    assert _guess_content_type("epub") == "ebook"
    assert _guess_content_type("pdf") == "ebook"
    assert _guess_content_type("mobi") == "ebook"


def test_guess_content_type_unknown():
    assert _guess_content_type("xyz") is None
    assert _guess_content_type(None) is None


def test_guess_content_type_from_filename():
    assert _guess_content_type("rar", "audiobook.m4b") == "audiobook"
    assert _guess_content_type("zip", "book.epub") == "ebook"


def test_parse_single_result_from_text():
    result = parse_single_result_from_text("Dune - Frank Herbert\nmp3 500MB 21h 14m")
    assert result is not None
    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert result.format == "mp3"
    assert result.size == "500MB"
    assert result.duration == "21h 14m"


def test_parse_single_result_from_empty_text():
    assert parse_single_result_from_text("") is None
    assert parse_single_result_from_text("   ") is None


def test_parse_bot_response_with_document_message():
    doc = SimpleNamespace(
        id=123456789,
        attributes=[SimpleNamespace(file_name="Dune.m4b", duration=76440)],
        size=536870912,
    )
    message = SimpleNamespace(
        id=42,
        text="",
        message="",
        document=doc,
        chat_id=999,
        peer_id=SimpleNamespace(user_id=999),
    )

    response = TelegramBotResponse(messages=[message])
    results = parse_bot_response(response)

    assert len(results) == 1
    result = results[0]
    assert result.title == "Dune"
    assert result.format == "m4b"
    assert result.has_document is True
    assert result.document_id == "123456789"
    assert result.message_id == 42
    assert result.chat_id == 999
    assert result.size_bytes == 536870912


def test_parse_bot_response_with_text_message():
    message = SimpleNamespace(
        id=42,
        text="Dune - Frank Herbert\nmp3 500MB 21h 14m",
        message="Dune - Frank Herbert\nmp3 500MB 21h 14m",
        document=None,
        chat_id=999,
        peer_id=SimpleNamespace(user_id=999),
    )

    response = TelegramBotResponse(messages=[message])
    results = parse_bot_response(response)

    assert len(results) == 1
    result = results[0]
    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert result.format == "mp3"
    assert result.size == "500MB"
    assert result.duration == "21h 14m"
    assert result.has_document is False


def test_parse_bot_response_empty():
    response = TelegramBotResponse(messages=[])
    results = parse_bot_response(response)
    assert results == []


def test_parse_bot_response_document_duration_formatting():
    doc = SimpleNamespace(
        id=111,
        attributes=[SimpleNamespace(file_name="Book.m4b", duration=3661)],
        size=100000,
    )
    message = SimpleNamespace(
        id=1,
        text="",
        message="",
        document=doc,
        chat_id=1,
        peer_id=SimpleNamespace(user_id=1),
    )

    response = TelegramBotResponse(messages=[message])
    results = parse_bot_response(response)

    assert len(results) == 1
    assert results[0].duration == "1h 1m"
