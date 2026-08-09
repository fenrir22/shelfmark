"""Telegram settings registration."""

from __future__ import annotations

from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)

logger = setup_logger(__name__)


def _test_connection(current_values: dict[str, Any] | None = None) -> dict[str, str | bool]:
    from .client import client_manager

    if not client_manager.is_connected:
        return {"success": False, "message": "Telegram client is not connected. Please authenticate first."}

    return client_manager.test_connection()


def _start_authentication(current_values: dict[str, Any] | None = None) -> dict[str, str | bool]:
    from shelfmark.core.config import config

    from .client import client_manager

    values = current_values or {}

    api_id_raw = values.get("TELEGRAM_API_ID") or config.get("TELEGRAM_API_ID", "")
    api_hash = values.get("TELEGRAM_API_HASH") or config.get("TELEGRAM_API_HASH", "")
    phone = values.get("TELEGRAM_PHONE") or config.get("TELEGRAM_PHONE", "")

    if not api_id_raw or not api_hash or not phone:
        return {
            "success": False,
            "message": "API ID, API Hash, and Phone number are required. Fill them in and save settings first.",
        }

    try:
        api_id = int(str(api_id_raw).strip())
    except (ValueError, TypeError):
        return {"success": False, "message": "API ID must be a valid number."}

    api_hash = str(api_hash).strip()
    phone = str(phone).strip()

    from shelfmark.config import env

    session_path = str(env.CONFIG_DIR / "telegram_session")

    connected = client_manager.connect(api_id, api_hash, session_path)
    
    if not connected and client_manager.status == "error":
        return {"success": False, "message": "Failed to connect to Telegram. Check your API credentials."}
    
    if not connected and client_manager.status != "auth_required":
        return {"success": False, "message": "Failed to connect to Telegram."}

    return client_manager.send_code(phone)


def _submit_code(current_values: dict[str, Any] | None = None) -> dict[str, str | bool]:
    from .client import client_manager

    values = current_values or {}
    code = str(values.get("TELEGRAM_AUTH_CODE", "")).strip()

    if not code:
        return {"success": False, "message": "Please enter the verification code from Telegram."}

    if not client_manager.auth_state.is_waiting_code:
        return {"success": False, "message": "No pending authentication. Click 'Send Code' first."}

    return client_manager.sign_in(code)


def _submit_2fa(current_values: dict[str, Any] | None = None) -> dict[str, str | bool]:
    from .client import client_manager

    values = current_values or {}
    password = str(values.get("TELEGRAM_2FA_PASSWORD", "")).strip()

    if not password:
        return {"success": False, "message": "Please enter your 2FA password."}

    if not client_manager.auth_state.is_waiting_2fa:
        return {"success": False, "message": "No pending 2FA authentication."}

    return client_manager.sign_in_2fa(password)


def _disconnect(current_values: dict[str, Any] | None = None) -> dict[str, str | bool]:
    from .client import client_manager

    client_manager.disconnect()
    return {"success": True, "message": "Disconnected from Telegram."}


def _clear_telegram_cache() -> dict[str, str | int | bool]:
    from .cache import clear_cache, get_cache_stats

    stats = get_cache_stats()
    count = clear_cache()
    return {
        "success": True,
        "message": f"Cleared {count} cached searches ({stats['total_releases']} releases)",
    }


@register_settings(
    name="telegram",
    display_name="Telegram",
    icon="download",
    order=57,
)
def telegram_settings() -> list[SettingsField]:
    return [
        HeadingField(
            key="heading",
            title="Telegram",
            description=(
                "Search and download releases from a Telegram bot using your Telegram account. "
                "This source uses MTProto (user client) to interact with a bot. "
                "You need Telegram API credentials (from https://my.telegram.org) and a valid Telegram account."
            ),
        ),
        CheckboxField(
            key="TELEGRAM_ENABLED",
            label="Enable Telegram",
            default=False,
            description="Enable the Telegram release source.",
        ),
        HeadingField(
            key="api_heading",
            title="Telegram API Credentials",
            description=(
                "Get your API ID and API Hash from [my.telegram.org](https://my.telegram.org). "
                "These are NOT the bot token — they identify your application to Telegram."
            ),
        ),
        TextField(
            key="TELEGRAM_API_ID",
            label="API ID",
            placeholder="e.g. 12345678",
            description="Your Telegram API ID (numeric).",
            required=True,
            env_supported=True,
        ),
        PasswordField(
            key="TELEGRAM_API_HASH",
            label="API Hash",
            placeholder="e.g. 0123456789abcdef0123456789abcdef",
            description="Your Telegram API Hash.",
            required=True,
            env_supported=True,
        ),
        HeadingField(
            key="account_heading",
            title="Telegram Account",
            description=(
                "Your Telegram account credentials. The account must be authorized to use the target bot. "
                "Your session is stored securely on the server and never exposed to the frontend."
            ),
        ),
        TextField(
            key="TELEGRAM_PHONE",
            label="Phone number",
            placeholder="e.g. +1234567890",
            description="Phone number associated with your Telegram account (with country code).",
            required=True,
            env_supported=True,
        ),
        TextField(
            key="TELEGRAM_AUTH_CODE",
            label="Verification code",
            placeholder="e.g. 12345",
            description="Enter the verification code received from Telegram after clicking 'Send Code'.",
            required=False,
            env_supported=False,
        ),
        PasswordField(
            key="TELEGRAM_2FA_PASSWORD",
            label="2FA password",
            placeholder="Your two-factor authentication password",
            description="Enter your 2FA password if prompted after entering the verification code.",
            required=False,
            env_supported=False,
        ),
        HeadingField(
            key="auth_heading",
            title="Authentication",
            description=(
                "Use the buttons below to authenticate your Telegram account. "
                "1. Save your API credentials and phone number first. "
                "2. Click 'Send Code' to receive a verification code. "
                "3. Enter the code and click 'Verify Code'. "
                "4. If 2FA is enabled, enter your password and click 'Verify 2FA'."
            ),
        ),
        ActionButton(
            key="telegram_send_code",
            label="Send Code",
            description="Send a verification code to your Telegram app. Requires API ID, API Hash, and phone number.",
            style="primary",
            callback=_start_authentication,
        ),
        ActionButton(
            key="telegram_verify_code",
            label="Verify Code",
            description="Verify the code received from Telegram.",
            style="primary",
            callback=_submit_code,
        ),
        ActionButton(
            key="telegram_verify_2fa",
            label="Verify 2FA",
            description="Verify your two-factor authentication password.",
            style="primary",
            callback=_submit_2fa,
        ),
        ActionButton(
            key="telegram_test_connection",
            label="Test Connection",
            description="Test the current Telegram connection.",
            callback=_test_connection,
        ),
        ActionButton(
            key="telegram_disconnect",
            label="Disconnect",
            description="Disconnect the Telegram session.",
            style="danger",
            callback=_disconnect,
        ),
        HeadingField(
            key="bot_heading",
            title="Bot Configuration",
            description="Configure which Telegram bot to use for searching.",
        ),
        TextField(
            key="TELEGRAM_BOT_USERNAME",
            label="Bot username",
            placeholder="e.g. @audiobooks_bot",
            description="The username of the Telegram bot to search through (with or without @).",
            required=True,
            env_supported=True,
        ),
        TextField(
            key="TELEGRAM_SEARCH_COMMAND",
            label="Search command format",
            placeholder="e.g. /search {query}",
            description=(
                "How to format search queries sent to the bot. "
                "Use {query} as placeholder for the search term. "
                "Leave empty to send the query as a plain message."
            ),
            required=False,
            env_supported=True,
        ),
        HeadingField(
            key="advanced_heading",
            title="Advanced",
            description="Advanced configuration options.",
        ),
        TextField(
            key="TELEGRAM_RESPONSE_TIMEOUT",
            label="Response timeout (seconds)",
            placeholder="60",
            description="How long to wait for the bot to respond to a search query.",
            required=False,
            env_supported=True,
        ),
        TextField(
            key="TELEGRAM_CACHE_TTL",
            label="Cache duration (seconds)",
            placeholder="604800",
            description="How long to cache search results. Default: 604800 (7 days). Set to 0 for forever.",
            required=False,
            env_supported=True,
        ),
        ActionButton(
            key="clear_telegram_cache",
            label="Clear Cache",
            description="Remove all cached Telegram search results.",
            style="danger",
            callback=_clear_telegram_cache,
        ),
    ]


@register_settings(
    name="telegram_group",
    display_name="Telegram Group",
    icon="users",
    order=58,
)
def telegram_group_settings() -> list[SettingsField]:
    return [
        HeadingField(
            key="heading",
            title="Telegram Group (silent search)",
            description=(
                "Search a Telegram group's existing message history for documents (PDFs, ebooks, "
                "manuals) and download them. This mode NEVER sends any message to the group: "
                "members cannot tell a bot is listening. Uses the same Telegram account as the "
                "Telegram source above (you must be a member of the group)."
            ),
        ),
        CheckboxField(
            key="TELEGRAM_GROUP_ENABLED",
            label="Enable Telegram Group search",
            default=False,
            description="Enable silent search of a Telegram group's history.",
        ),
        TextField(
            key="TELEGRAM_GROUP_USERNAME",
            label="Group username",
            placeholder="e.g. @rpg_manuals",
            description=(
                "The group to search. Use the username (with or without @), a numeric chat ID, "
                "an invite link, or the group's display title (e.g. 'The Amber Room request "
                "and submissions'). The connected account must already be a member of the group."
            ),
            required=True,
            env_supported=True,
        ),
        TextField(
            key="TELEGRAM_GROUP_CHANNEL",
            label="Channel / topic name",
            placeholder="e.g. request and submission",
            description=(
                "Optional: the channel or forum topic inside the group that contains the files "
                "(e.g. '#request and submission'). Only that channel/topic is searched. "
                "Leave empty to search the whole group."
            ),
            required=False,
            env_supported=True,
        ),
        TextField(
            key="TELEGRAM_GROUP_SEARCH_LIMIT",
            label="Max results",
            placeholder="50",
            description="Maximum number of matching messages to consider per search.",
            required=False,
            env_supported=True,
        ),
    ]
