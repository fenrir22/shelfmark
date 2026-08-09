"""Custom site branding (logo / favicon) storage and serving helpers.

Uploaded images are stored inside the config directory so they survive
container rebuilds. The static ``/logo.png`` and ``/favicon.ico`` routes
fall back to these custom files when present, so no frontend rebuild is
needed to change the site identity.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

from werkzeug.utils import secure_filename

from shelfmark.config.env import CONFIG_DIR
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from werkzeug.datastructures import FileStorage

logger = setup_logger(__name__)

ALLOWED_KINDS = ("logo", "favicon", "mascot")

# Only raster formats are allowed; SVG is rejected to avoid script injection.
ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/vnd.microsoft.icon",
    "image/x-icon",
    "image/avif",
}

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB

_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/vnd.microsoft.icon": ".ico",
    "image/x-icon": ".ico",
    "image/avif": ".avif",
}


def get_assets_dir() -> Path:
    """Return the branding assets directory (creating it on demand)."""
    assets_dir = CONFIG_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    return assets_dir


def get_custom_asset_path(kind: str) -> Path | None:
    """Return the custom asset file path for ``kind``, or None if unset."""
    if kind not in ALLOWED_KINDS:
        return None
    assets_dir = get_assets_dir()
    matches = sorted(assets_dir.glob(f"{kind}.*"))
    if not matches:
        return None
    # Prefer the most recently modified file (e.g. logo.png over logo.jpg).
    return max(matches, key=lambda p: p.stat().st_mtime)


def get_custom_asset_mimetype(path: Path) -> str:
    """Guess the content type for an uploaded branding asset."""
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    ext = path.suffix.lower()
    if ext == ".ico":
        return "image/vnd.microsoft.icon"
    return "application/octet-stream"


def save_asset(kind: str, file: FileStorage) -> tuple[bool, str]:
    """Validate and store an uploaded logo/favicon image.

    Returns:
        ``(success, message)``. On success the previously uploaded file for
        the same kind is replaced.

    """
    if kind not in ALLOWED_KINDS:
        return False, f"Unknown asset kind: {kind}"

    if not file or not file.filename:
        return False, "No file provided."

    # Reject by content-type first, then by extension as a fallback.
    content_type = (file.mimetype or "").lower()
    if content_type not in ALLOWED_MIME_TYPES:
        extension = Path(secure_filename(file.filename or "")).suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".avif"}:
            return False, (
                "Unsupported file type. Allowed formats: PNG, JPEG, WebP, GIF, ICO, AVIF."
            )
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(extension, mimetypes.guess_type(f"x{extension}")[0] or "image/png")

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size <= 0:
        return False, "The uploaded file is empty."
    if size > MAX_UPLOAD_BYTES:
        return False, "The uploaded image must be smaller than 2 MB."

    extension = _MIME_EXTENSIONS.get(content_type, ".png")
    target = get_assets_dir() / f"{kind}{extension}"
    try:
        file.save(target)
    except OSError as e:
        logger.exception("Failed to save %s asset", kind)
        return False, f"Failed to save asset: {e!s}"

    # Remove any previously uploaded files for this kind with other extensions.
    for stale in get_assets_dir().glob(f"{kind}.*"):
        if stale.name != target.name:
            try:
                stale.unlink()
            except OSError:
                logger.warning("Failed to remove stale asset %s", stale)

    logger.info("Uploaded custom %s asset: %s", kind, target)
    return True, f"{kind.capitalize()} updated successfully."


def reset_asset(kind: str) -> tuple[bool, str]:
    """Remove the custom asset for ``kind``, restoring the built-in default."""
    if kind not in ALLOWED_KINDS:
        return False, f"Unknown asset kind: {kind}"

    removed = False
    for stale in get_assets_dir().glob(f"{kind}.*"):
        try:
            stale.unlink()
            removed = True
        except OSError:
            logger.warning("Failed to remove asset %s", stale)

    if removed:
        logger.info("Reset %s asset to default", kind)
        return True, f"{kind.capitalize()} reset to default."
    return True, f"{kind.capitalize()} is already using the default."


def branding_status() -> dict[str, Any]:
    """Describe which custom assets are currently uploaded."""
    return {
        kind: get_custom_asset_path(kind) is not None for kind in ALLOWED_KINDS
    }
