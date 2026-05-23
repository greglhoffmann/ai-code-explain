"""Shared utility helpers for the sandbox demo application.

This file is deliberately kept mostly correct but with a few subtle issues
so the LLM has something interesting to note when it is loaded as local
filesystem context.
"""

import re
import datetime
from typing import Optional


# Missing raw-string prefix — \. is not a valid escape sequence in a plain string,
# though CPython happens to treat unknown escapes as literals. Use r"..." to be explicit.
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
    # Replaces only a narrow set — does not guard against reserved Windows names
    # (CON, PRN, AUX …) or leading/trailing dots/spaces.
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
