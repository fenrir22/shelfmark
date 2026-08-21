"""External Cloudflare bypasser using FlareSolverr."""

import random
import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from shelfmark.bypass import BypassCancelledError
from shelfmark.bypass.cookie_store import store_extracted_cookies
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify

if TYPE_CHECKING:
    from collections.abc import Mapping
    from threading import Event

    from shelfmark.download import network

logger = setup_logger(__name__)
_RNG = random.SystemRandom()

# Timeout constants (seconds)
CONNECT_TIMEOUT = 10
MAX_READ_TIMEOUT = 120
READ_TIMEOUT_BUFFER = 15

# Retry settings
MAX_RETRY = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 10.0

# Cookie storage - shared with requests library for Cloudflare bypass
_cf_cookies: dict[str, dict] = {}
_cf_cookies_lock = threading.Lock()

# User-Agent storage - Cloudflare ties cf_clearance to the UA that solved the challenge
_cf_user_agents: dict[str, str] = {}

# Protection cookie names we care about (Cloudflare and DDoS-Guard)
CF_COOKIE_NAMES = {"cf_clearance", "__cf_bm", "cf_chl_2", "cf_chl_prog"}
DDG_COOKIE_NAMES = {
    "__ddg1_",
    "__ddg2_",
    "__ddg5_",
    "__ddg8_",
    "__ddg9_",
    "__ddg10_",
    "__ddgid_",
    "__ddgmark_",
    "ddg_last_challenge",
}


def _get_base_domain(domain: str) -> str:
    """Extract base domain from hostname (e.g., 'www.example.com' -> 'example.com')."""
    return ".".join(domain.split(".")[-2:]) if "." in domain else domain


def _store_cookies_from_flaresolverr(url: str, cookies: list[dict], user_agent: str | None) -> None:
    """Store cookies and user agent from FlareSolverr response."""
    if not cookies:
        return
    
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    base_domain = _get_base_domain(hostname)
    
    # Filter and store cookies
    cookie_dict = {}
    for cookie in cookies:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name and value:
            # Store all protection cookies
            if name in CF_COOKIE_NAMES or name in DDG_COOKIE_NAMES or name.startswith("cf_") or name.startswith("__ddg"):
                cookie_dict[name] = value
    
    if cookie_dict:
        with _cf_cookies_lock:
            _cf_cookies[base_domain] = cookie_dict
            logger.debug("Stored %d cookies for %s from FlareSolverr", len(cookie_dict), base_domain)
    
    # Store user agent
    if user_agent:
        with _cf_cookies_lock:
            _cf_user_agents[base_domain] = user_agent
            logger.debug("Stored user agent for %s from FlareSolverr", base_domain)


def get_cf_cookies_for_domain(domain: str) -> dict[str, str]:
    """Get CF cookies for a domain."""
    base_domain = _get_base_domain(domain)
    with _cf_cookies_lock:
        return _cf_cookies.get(base_domain, {})


def get_cf_user_agent_for_domain(domain: str) -> str | None:
    """Get CF user agent for a domain."""
    base_domain = _get_base_domain(domain)
    with _cf_cookies_lock:
        return _cf_user_agents.get(base_domain)


def _coerce_config_str(value: object, default: str) -> str:
    """Return a string config value or a safe default."""
    if isinstance(value, str):
        return value
    return default


def _coerce_timeout_ms(value: object, default: int) -> int:
    """Return a positive timeout in milliseconds or the default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def max_duration_seconds() -> float:
    """Upper bound on how long get_bypassed_page() can take for one URL.

    MAX_RETRY attempts at the configured read timeout, plus the exponential backoff waited
    between them (jitter is < 1s per gap, counted as a full second to stay conservative).
    Callers use this to declare a stall-detection grace; see shelfmark.download.activity.
    """
    bypasser_timeout = _coerce_timeout_ms(config.get("EXT_BYPASSER_TIMEOUT", 60000), 60000)
    read_timeout = min((bypasser_timeout / 1000) + READ_TIMEOUT_BUFFER, MAX_READ_TIMEOUT)
    backoff_total = sum(
        min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) + 1.0
        for attempt in range(1, MAX_RETRY)
    )
    return MAX_RETRY * read_timeout + backoff_total


def _store_solution_clearance(target_url: str, solution: Mapping[str, Any]) -> None:
    """Keep the clearance the solver won, so later requests do not re-solve.

    A solve is the expensive part of an external bypass - tens of seconds of real
    browser - and FlareSolverr-compatible services hand back the cookies and the
    User-Agent that earned it. Dropping them meant every single request paid a 403
    plus a full solve, and a file download (which the solver cannot proxy, being
    binary) never presented clearance at all.

    The UA matters as much as the cookies: Cloudflare ties cf_clearance to the UA
    that solved the challenge, so replaying the cookie under our own UA is rejected.
    """
    cookies = solution.get("cookies") or []
    if not isinstance(cookies, list):
        logger.debug("External bypasser returned no usable cookie list for '%s'", target_url)
        return

    user_agent = solution.get("userAgent")
    store_extracted_cookies(
        url=target_url,
        cookies=cookies,
        user_agent=user_agent if isinstance(user_agent, str) else None,
    )


def _fetch_via_bypasser(target_url: str) -> str | None:
    """Make a single request to the external bypasser service. Returns HTML or None."""
    raw_bypasser_url = _coerce_config_str(
        config.get("EXT_BYPASSER_URL", "http://flaresolverr:8191"),
        "http://flaresolverr:8191",
    )
    bypasser_path = _coerce_config_str(config.get("EXT_BYPASSER_PATH", "/v1"), "/v1")
    bypasser_timeout = _coerce_timeout_ms(config.get("EXT_BYPASSER_TIMEOUT", 60000), 60000)

    bypasser_url = normalize_http_url(raw_bypasser_url)
    if not bypasser_url or not bypasser_path:
        logger.error(
            "External bypasser not configured. Check EXT_BYPASSER_URL and EXT_BYPASSER_PATH."
        )
        return None

    read_timeout = min((bypasser_timeout / 1000) + READ_TIMEOUT_BUFFER, MAX_READ_TIMEOUT)

    try:
        response = requests.post(
            f"{bypasser_url}{bypasser_path}",
            headers={"Content-Type": "application/json"},
            json={
                "cmd": "request.get",
                "url": target_url,
                "maxTimeout": bypasser_timeout,
            },
            timeout=(CONNECT_TIMEOUT, read_timeout),
            verify=get_ssl_verify(bypasser_url),
        )
        response.raise_for_status()
        result = response.json()

        status = result.get("status", "unknown")
        message = result.get("message", "")
        logger.debug("External bypasser response for '%s': %s - %s", target_url, status, message)

        if status != "ok":
            logger.warning(
                "External bypasser failed for '%s': %s - %s",
                target_url,
                status,
                message,
            )
            return None

        solution = result.get("solution")
        html = solution.get("response", "") if solution else ""

        if not html:
            logger.warning("External bypasser returned empty response for '%s'", target_url)
            return None

        try:
            _store_solution_clearance(target_url, solution)
        except AttributeError, KeyError, TypeError, ValueError:
            # Storing clearance is an optimisation; the page is the product. The
            # solution JSON comes from a service we do not control, so a surprise in
            # its cookie shape must not discard HTML that already cost a ~30s solve
            # and send the caller round for up to MAX_RETRY more of them.
            logger.debug("Could not store bypass clearance for '%s'", target_url, exc_info=True)

    except requests.exceptions.Timeout:
        logger.warning(
            "External bypasser timed out for '%s' (connect: %ss, read: %.0fs)",
            target_url,
            CONNECT_TIMEOUT,
            read_timeout,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("External bypasser request failed for '%s': %s", target_url, e)
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("External bypasser returned malformed response for '%s': %s", target_url, e)
    else:
        return html

    return None


def _check_cancelled(cancel_flag: Event | None, context: str) -> None:
    """Check if operation was cancelled and raise exception if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info("External bypasser cancelled %s", context)
        msg = "Bypass cancelled"
        raise BypassCancelledError(msg)


def _sleep_with_cancellation(seconds: float, cancel_flag: Event | None) -> None:
    """Sleep for the specified duration, checking for cancellation each second."""
    for _ in range(int(seconds)):
        _check_cancelled(cancel_flag, "during backoff")
        time.sleep(1)
    remaining = seconds - int(seconds)
    if remaining > 0:
        time.sleep(remaining)


def get_bypassed_page(
    url: str,
    selector: network.AAMirrorSelector | None = None,
    cancel_flag: Event | None = None,
) -> str | None:
    """Fetch HTML via external bypasser with retries and mirror rotation."""
    from shelfmark.download import network as network_module

    sel = selector or network_module.AAMirrorSelector()

    for attempt in range(1, MAX_RETRY + 1):
        _check_cancelled(cancel_flag, "by user")

        attempt_url = sel.rewrite(url)
        result = _fetch_via_bypasser(attempt_url)
        if result:
            return result

        if attempt == MAX_RETRY:
            break

        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) + _RNG.random()
        logger.info(
            "External bypasser attempt %s/%s failed, retrying in %.1fs",
            attempt,
            MAX_RETRY,
            delay,
        )

        _sleep_with_cancellation(delay, cancel_flag)

        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            logger.info("Rotated %s for retry", action)

    return None
