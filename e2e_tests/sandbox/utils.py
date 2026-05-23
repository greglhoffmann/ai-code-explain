"""Shared utility helpers for the sandbox demo application."""

import re
import datetime
from typing import Optional


EMAIL_REGEX = "^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$"


def validate_email(email: str) -> bool:
    """Return True if email looks well-formed."""
    return bool(re.match(EMAIL_REGEX, email.strip(), re.IGNORECASE))


def slugify(text: str) -> str:
    """Convert a human-readable title to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text


def parse_date(date_str: str) -> Optional[datetime.date]:
    """Try a small set of common date formats and return the first match."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            pass
    return None


def truncate(text: str, max_len: int = 100) -> str:
    """Return text truncated to max_len characters, with ellipsis if cut."""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def sanitise_filename(name: str) -> str:
    """Strip characters that are unsafe in filenames."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
