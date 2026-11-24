"""Step identifiers.

An ID is 7 capital letters (A-Z, base 26) encoding the whole seconds elapsed since
2026-11-11 00:00:00 UTC. They sort in chronological order, cover about 254 years, and
are always computed from UTC so that daylight saving or the time zone of the user
cannot create duplicates or disorder.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

ORIGIN = datetime(2026, 11, 11, tzinfo=timezone.utc)
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
WIDTH = 7
CAPACITY = len(ALPHABET) ** WIDTH
LABEL_MAX = 30

_ID_RE = re.compile(rf"^[A-Z]{{{WIDTH}}}$")
_PREFIX_RE = re.compile(rf"^([A-Za-z]{{{WIDTH}}})(?=_|$)")


class StepIdError(ValueError):
    pass


def normalize(text: str) -> str:
    return text.strip().upper()


def is_valid(text: str) -> bool:
    return bool(_ID_RE.match(normalize(text)))


def encode(seconds: int) -> str:
    if not 0 <= seconds < CAPACITY:
        raise StepIdError(f"{seconds} seconds cannot be encoded on {WIDTH} letters")
    letters = []
    for _ in range(WIDTH):
        seconds, rest = divmod(seconds, len(ALPHABET))
        letters.append(ALPHABET[rest])
    return "".join(reversed(letters))


def decode(step_id: str) -> int:
    s = normalize(step_id)
    if not is_valid(s):
        raise StepIdError(f"'{step_id}' is not a valid step ID")
    n = 0
    for ch in s:
        n = n * len(ALPHABET) + ALPHABET.index(ch)
    return n


def from_datetime(when: datetime) -> str:
    """ID of a moment (a timezone-aware datetime, converted to UTC)."""
    if when.tzinfo is None:
        raise StepIdError("A timezone-aware datetime is required")
    seconds = math.floor((when.astimezone(timezone.utc) - ORIGIN).total_seconds())
    if seconds < 0:
        raise StepIdError(f"The system clock is set before {ORIGIN:%Y-%m-%d}")
    return encode(seconds)


def to_datetime(step_id: str) -> datetime:
    """Start time (UTC) encoded in an ID."""
    return ORIGIN + timedelta(seconds=decode(step_id))


def now_id() -> str:
    return from_datetime(datetime.now(timezone.utc))


def next_id(step_id: str) -> str:
    """The following second; used when an ID is already taken."""
    return encode(decode(step_id) + 1)


def parse_folder_name(name: str) -> str | None:
    """'AACJWTH_split_fcs' or 'AACJWTH' -> 'AACJWTH'; None if the name has no ID."""
    m = _PREFIX_RE.match(name)
    return m.group(1).upper() if m else None


def sanitize_label(label: str | None, default: str) -> str:
    """Folder-safe label (letters, digits, dot, dash, underscore), at most LABEL_MAX long."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", (label or "").strip())
    text = re.sub(r"_+", "_", text).strip("._-")[:LABEL_MAX].strip("._-")
    return text or default


def folder_name(step_id: str, label: str | None, default: str) -> str:
    """Step folder name: <id>_<user label, else the plugin id>. Fixed once created."""
    return f"{normalize(step_id)}_{sanitize_label(label, default)}"
