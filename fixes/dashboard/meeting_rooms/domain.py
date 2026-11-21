"""Domain helpers: time parsing, validation and overlap logic.

All times are stored as ISO-8601 strings in a single fixed timezone-less
format: 'YYYY-MM-DDTHH:MM'. Because this format is zero-padded and
lexicographically ordered, plain string comparison is a correct ordering
(and therefore correct for overlap checks).

Timezone assumption: every booking is interpreted in one local
(timezone-unaware) time zone. The README documents this.
"""

from __future__ import annotations

import re
from datetime import date as _date

_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class ValidationError(ValueError):
    """Raised when user supplied date/time input is invalid."""


class OverlapError(ValueError):
    """Raised when a booking would overlap an existing active booking."""


class NotFoundError(LookupError):
    """Raised when a room or booking id does not exist."""


def parse_date(value: str) -> str:
    """Validate a 'YYYY-MM-DD' date string and return it unchanged."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValidationError(f"Invalid date {value!r}, expected YYYY-MM-DD")
    y, m, d = (int(p) for p in value.split("-"))
    try:
        _date(y, m, d)
    except ValueError as exc:
        raise ValidationError(f"Invalid date {value!r}: {exc}") from exc
    return value


def parse_time(value: str) -> str:
    """Validate a 'HH:MM' time string and return zero-padded 'HH:MM'."""
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise ValidationError(f"Invalid time {value!r}, expected HH:MM")
    hh, mm = (int(p) for p in value.split(":"))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValidationError(f"Invalid time {value!r}, expected 00:00-23:59")
    return f"{hh:02d}:{mm:02d}"


def combine(date_str: str, time_str: str) -> str:
    """Combine a validated date and time into an ISO datetime string."""
    return f"{parse_date(date_str)}T{parse_time(time_str)}"


def parse_dt(value: str) -> str:
    """Validate a full 'YYYY-MM-DDTHH:MM' datetime and return it unchanged."""
    if not isinstance(value, str) or not _DT_RE.match(value):
        raise ValidationError(
            f"Invalid datetime {value!r}, expected YYYY-MM-DDTHH:MM"
        )
    date_part, time_part = value.split("T")
    parse_date(date_part)
    parse_time(time_part)
    return value


def validate_range(start: str, end: str) -> tuple:
    """Validate a [start, end) datetime range.

    A zero-length or negative range (end <= start) is rejected with a clear
    message. Both bounds must be valid ISO datetimes.
    """
    start = parse_dt(start)
    end = parse_dt(end)
    if end <= start:
        raise ValidationError(
            f"End time {end} must be after start time {start} "
            "(zero-length or reversed ranges are not allowed)"
        )
    return start, end


def overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    """Return True iff half-open ranges [a) and [b) intersect.

    Overlap rule: existing.start < new.end AND new.start < existing.end.
    Purely adjacent ranges (end_a == start_b or end_b == start_a) do NOT
    overlap and must be allowed (back-to-back bookings).
    """
    return start_a < end_b and start_b < end_a
