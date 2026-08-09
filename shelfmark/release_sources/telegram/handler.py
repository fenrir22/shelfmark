"""Telegram download handler.

Downloads files from Telegram messages via MTProto user client.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import DownloadHandler, register_handler

from .client import client_manager

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)


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


@register_handler("telegram")
class TelegramDownloadHandler(DownloadHandler):
    def download(
        self,
        task: DownloadTask,
        cancel_flag: threading.Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        if not client_manager.is_connected:
            logger.warning("Telegram client is not connected")
            status_callback("failed", "Telegram client is not connected")
            return None

        task_source_context = task.retry_source_context or {}
        message_id = task_source_context.get("message_id")
        chat_id = task_source_context.get("chat_id")
        callback_data = task_source_context.get("callback_data")

        if not message_id or not chat_id:
            logger.warning("Telegram download missing message_id or chat_id in source context")
            status_callback("failed", "Missing Telegram message information")
            return None

        try:
            # Check if we need to click a callback button first (book-list style)
            if callback_data and not task_source_context.get("has_document"):
                status_callback("resolving", "Requesting file from bot")
                logger.info("Clicking callback button: %s", callback_data)
                
                from .source import TelegramSource
                source = TelegramSource()
                
                doc_info = source.download_via_callback(
                    callback_data=callback_data,
                    message_id=int(message_id),
                    chat_id=int(chat_id),
                )
                
                if doc_info is None:
                    status_callback("error", "Bot did not send the file")
                    return None
                
                # Update message_id to the document message
                message_id = doc_info["message_id"]
                task_source_context["file_name"] = doc_info.get("file_name")
            
            status_callback("resolving", "Resolving Telegram message")

            message = self._resolve_message(int(chat_id), int(message_id))
            if message is None:
                status_callback("error", "Could not find Telegram message")
                return None

            if not message.document:
                status_callback("error", "Message has no document")
                return None

            file_name = task_source_context.get("file_name") or task.format or "download"
            ext = Path(file_name).suffix.lstrip(".") or task.format or "bin"

            from shelfmark.download.staging import get_staging_path

            staging_path = get_staging_path(task.task_id, ext)

            status_callback("downloading", "")

            total_size = getattr(message.document, "size", None)

            def _progress(current: int, total: int) -> None:
                if cancel_flag.is_set():
                    return
                if total > 0:
                    progress_callback(current / total * 100)
                elif total_size and total_size > 0:
                    progress_callback(current / total_size * 100)

            result = client_manager.download_media(
                message=message,
                output_path=str(staging_path),
                progress_callback=_progress,
                cancel_flag=cancel_flag,
            )

            if cancel_flag.is_set():
                staging_path.unlink(missing_ok=True)
                status_callback("cancelled", "Cancelled")
                return None

            if result is None:
                status_callback("error", "Download failed")
                return None

            downloaded_path = Path(result)
            if not downloaded_path.exists():
                status_callback("error", "Downloaded file not found")
                return None

            if downloaded_path != staging_path:
                import shutil

                shutil.move(str(downloaded_path), str(staging_path))

            if not staging_path.exists() or staging_path.stat().st_size == 0:
                status_callback("error", "Downloaded file is empty")
                staging_path.unlink(missing_ok=True)
                return None

            logger.info("Telegram download complete: %s", staging_path)
            
            # Extract archive if it's a ZIP or RAR file
            if staging_path.suffix.lower() in {".zip", ".rar"}:
                status_callback("extracting", "Extracting archive")
                extracted_folder = self._extract_archive(staging_path, task.title)
                if extracted_folder:
                    logger.info("Archive extracted to: %s", extracted_folder)
                    return str(extracted_folder)
                else:
                    logger.warning("Failed to extract archive, returning original file")
            
            return str(staging_path)

        except Exception as e:
            logger.exception("Telegram download failed")
            status_callback("error", f"Download failed: {e}")
            return None

    def _resolve_message(self, chat_id: int, message_id: int) -> Any:
        try:
            loop = client_manager._ensure_loop()

            import asyncio

            async def _get_message() -> Any:
                return await client_manager._client.get_messages(chat_id, ids=message_id)

            future = asyncio.run_coroutine_threadsafe(_get_message(), loop)
            return future.result(timeout=30)
        except Exception:
            logger.exception("Failed to resolve Telegram message %s/%s", chat_id, message_id)
            return None

    def _extract_archive(self, archive_path: Path, audiobook_title: str) -> Path | None:
        """Extract ZIP or RAR archive to a folder with the audiobook name.
        
        Args:
            archive_path: Path to the archive file
            audiobook_title: Title of the audiobook (used for folder name)
            
        Returns:
            Path to the extracted folder, or None if extraction failed
        """
        import re
        import shutil
        import zipfile
        
        try:
            # Create a safe folder name from the title
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', audiobook_title)
            safe_title = safe_title.strip('. ')
            if not safe_title:
                safe_title = "audiobook"
            
            # Create extraction folder in the same directory as the archive
            extract_dir = archive_path.parent / safe_title
            extract_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info("Extracting %s to %s", archive_path.name, extract_dir)
            
            # Extract based on file type
            if archive_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
            elif archive_path.suffix.lower() == ".rar":
                import rarfile
                with rarfile.RarFile(archive_path, 'r') as rar_ref:
                    rar_ref.extractall(extract_dir)
            else:
                logger.warning("Unsupported archive format: %s", archive_path.suffix)
                return None
            
            # Remove the original archive file after successful extraction
            archive_path.unlink()
            logger.info("Removed original archive: %s", archive_path.name)
            
            return extract_dir
            
        except zipfile.BadZipFile as e:
            logger.error("Invalid ZIP file: %s", e)
            return None
        except Exception as e:
            logger.exception("Failed to extract archive: %s", e)
            # Clean up partial extraction
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            return None

    def cancel(self, task_id: str) -> bool:
        logger.debug("Cancel requested for Telegram task: %s", task_id)
        return True

    def build_retry_resolution_fields(self, release_data: dict) -> dict:
        extra = release_data.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}
        return {
            "retry_source_context": {
                "message_id": extra.get("message_id"),
                "chat_id": extra.get("chat_id"),
                "file_name": extra.get("file_name"),
                "callback_data": extra.get("callback_data"),
                "has_document": extra.get("has_document"),
            },
        }


@register_handler("telegram_group")
class TelegramGroupDownloadHandler(TelegramDownloadHandler):
    """Download handler for silent Telegram group search results.

    Documents found in group history carry the document directly, so the
    shared download logic (resolve message -> download media) applies as-is.
    """
