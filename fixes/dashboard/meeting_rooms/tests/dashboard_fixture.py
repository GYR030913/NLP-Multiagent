"""Fixed labelled fixture for dashboard analytics tests (t17).

This module is intentionally NOT named test_*.py so unittest discovery does
not auto-collect it. It provides a deterministic, hand-computed fixture that
is clearly SEPARATED from real snapshot rows (dataset ds1,
inputs/bookings-71faff1dfce60864.sqlite3, source sha256
71faff1d...9224b). Nothing here is read from the session database.

Conventions (operator continuity test, treated as fixed):
  * timezone Asia/Hong_Kong local wall-clock
  * selected start/end dates INCLUSIVE; window = [start 00:00, end+1day 00:00)
  * a booking is counted when its interval overlaps the window [start, end)
  * cancelled rows are counted separately, excluded from active/availability
  * reserved hours = sum of active interval intersections CLIPPED to window,
    expressed in hours
  * peak day = calendar date with most overlapping active bookings
  * peak hour = hour-of-day (0-23) with most overlapping active bookings
  * cancellation rate = cancelled / ALL bookings overlapping the window

Expected values below were computed BY HAND from the rows in this file and
are the independent reference the tests must match.
"""

# --- Window (inclusive dates, Asia/Hong_Kong) -----------------------------
TZ = "Asia/Hong_Kong"
WINDOW_START = "2026-11-10"  # inclusive
WINDOW_END = "2026-11-12"    # inclusive
# Effective half-open window: [2026-11-10T00:00, 2026-11-13T00:00)

# --- Rooms (synthetic fixture ids; independent of session DB) -------------
ROOMS = [
    {"id": 1, "name": "Alpha (fixture)"},
    {"id": 2, "name": "Beta (fixture)"},
    {"id": 3, "name": "Gamma (fixture)"},
]

# --- Bookings (local naive wall-clock strings, labelled fixture) ----------
# f7 clips into the window from the previous day.
FIXTURE_BOOKINGS = [
    {"id": 1, "room_id": 1, "start_dt": "2026-11-10T09:00", "end_dt": "2026-11-10T11:00", "status": "active", "label": "fixture"},
    {"id": 2, "room_id": 1, "start_dt": "2026-11-11T14:00", "end_dt": "2026-11-11T16:00", "status": "active", "label": "fixture"},
    {"id": 3, "room_id": 2, "start_dt": "2026-11-10T09:30", "end_dt": "2026-11-10T10:30", "status": "active", "label": "fixture"},
    {"id": 4, "room_id": 2, "start_dt": "2026-11-12T09:00", "end_dt": "2026-11-12T10:00", "status": "active", "label": "fixture"},
    {"id": 5, "room_id": 3, "start_dt": "2026-11-10T10:00", "end_dt": "2026-11-10T12:00", "status": "cancelled", "label": "fixture"},
    {"id": 6, "room_id": 1, "start_dt": "2026-11-13T09:00", "end_dt": "2026-11-13T10:00", "status": "active", "label": "fixture"},  # no window overlap
    {"id": 7, "room_id": 2, "start_dt": "2026-11-09T23:00", "end_dt": "2026-11-10T01:00", "status": "active", "label": "fixture"},  # clips to 1h
]

# --- Hand-computed expectations -------------------------------------------
# Active bookings overlapping window: ids 1,2,3,4,7  (5 total)
# Cancelled overlapping window: id 5 (1). id 6 has NO overlap -> excluded.
# Denominator (ALL overlapping bookings) = 5 + 1 = 6.
EXPECTED = {
    "per_room": {
        1: {"active_count": 2, "reserved_hours": 4.0},  # 2h + 2h
        2: {"active_count": 3, "reserved_hours": 3.0},  # 1h + 1h + clipped 1h
        3: {"active_count": 0, "reserved_hours": 0.0},  # only cancelled row
    },
    "peak_day": {"date": "2026-11-10", "count": 3},   # ids 1,3,7
    "peak_hour": {"hour": 9, "count": 3},             # hour 9: ids 1,3,4
    "cancellations": {"count": 1, "denominator": 6, "rate": 1 / 6},
}

# Sanity guard used by the real tests: totals derived independently.
EXPECTED["total_active_overlapping"] = 5
EXPECTED["total_reserved_hours"] = 7.0  # 4.0 + 3.0 + 0.0


def reason():
    """One-line label used in failure messages to separate fixture vs real."""
    return "FIXTURE (labelled synthetic rows) - NOT real snapshot ds1"
