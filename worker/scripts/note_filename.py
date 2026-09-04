"""Shared helpers for adding a publication-date prefix to note file names.

Several scripts need the same small behavior: given a ``published`` value
(ISO-8601 with or without timezone, or a plain date), compute a ``YYYY-MM-DD``
date and prepend it to a Markdown file name. Keeping the logic in one place
guarantees the convert step, the Qwen organize step and the batch rename all
agree on the exact file name they produce.
"""

from __future__ import annotations

import re
from datetime import datetime

# A leading "YYYY-MM-DD-" prefix on a file name.
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
# A leading ISO date (used to pull the date out of a published value).
_LEADING_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

_TRUE_VALUES = {"1", "true", "yes", "on"}


def flag_enabled(value: object) -> bool:
    """Interpret a config/env value as a boolean flag."""
    return str(value or "").strip().lower() in _TRUE_VALUES


def parse_published_date(published: object) -> str:
    """Return the ``YYYY-MM-DD`` date from a published value, or ``""``.

    The published value is expected to already be in the note's local timezone
    (Bilibili uses ``+08:00``), so the leading date is used as-is. Handles ISO
    timestamps with or without timezone and plain ``YYYY-MM-DD`` dates.
    """
    value = str(published or "").strip().strip('"').strip("'")
    if not value:
        return ""
    match = _LEADING_DATE_RE.match(value)
    if not match:
        return ""
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        datetime(year, month, day)
    except ValueError:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def has_date_prefix(name: str) -> bool:
    """Return True when the file name already starts with a date prefix."""
    return _DATE_PREFIX_RE.match(name) is not None


def strip_date_prefix(name: str) -> str:
    """Remove a leading ``YYYY-MM-DD-`` prefix, if present."""
    match = _DATE_PREFIX_RE.match(name)
    if not match:
        return name
    return name[match.end():]


def prepend_date_prefix(name: str, date: str) -> str:
    """Prepend a ``YYYY-MM-DD`` date to a file name (idempotent).

    Returns ``name`` unchanged when ``date`` is empty or the name already
    starts with a date prefix, so the operation is safe to re-run.
    """
    date = str(date or "").strip()
    if not date or has_date_prefix(name):
        return name
    return f"{date}-{name}"
