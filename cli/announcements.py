import getpass
import logging

import requests
from rich.console import Console
from rich.panel import Panel

from cli.config import CLI_CONFIG

logger = logging.getLogger(__name__)


def fetch_announcements(url: str | None = None, timeout: float | None = None) -> dict:
    """Fetch announcements from endpoint. Returns dict with announcements and settings."""
    endpoint = url or CLI_CONFIG["announcements_url"]
    timeout = timeout or CLI_CONFIG["announcements_timeout"]
    fallback = CLI_CONFIG["announcements_fallback"]

    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        # Network/HTTP/JSON failures degrade to the fallback banner; they are
        # expected in offline environments. Programming errors still raise.
        logger.warning("Announcements fetch failed (%s: %s); using fallback", type(exc).__name__, exc)
        return {
            "announcements": [fallback],
            "require_attention": False,
        }

    # Validate the payload shape: the endpoint may return a non-list or a
    # list containing non-strings, which would break "\n".join downstream.
    announcements = data.get("announcements", [fallback])
    if not isinstance(announcements, list) or not all(isinstance(a, str) for a in announcements):
        logger.warning("Announcements payload malformed (got %r); using fallback", announcements)
        announcements = [fallback]

    return {
        "announcements": announcements,
        "require_attention": bool(data.get("require_attention", False)),
    }


def display_announcements(console: Console, data: dict) -> None:
    """Display announcements panel. Prompts for Enter if require_attention is True."""
    announcements = data.get("announcements", [])
    require_attention = data.get("require_attention", False)

    if not announcements:
        return

    content = "\n".join(str(a) for a in announcements)

    panel = Panel(
        content,
        border_style="cyan",
        padding=(1, 2),
        title="Announcements",
    )
    console.print(panel)

    if require_attention:
        getpass.getpass("Press Enter to continue...")
    else:
        console.print()
