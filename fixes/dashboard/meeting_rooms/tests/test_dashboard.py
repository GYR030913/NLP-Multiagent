import unittest
import os as _os, sys as _sys
# Make the bundle's own package win over any stale sibling copy on sys.path
# (e.g. /workspace/meeting_rooms, which previously shadowed this bundle and
#  broke `from meeting_rooms import analytics`, cf. t17 evidence seq 1081).
_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
while _ROOT in _sys.path:
    _sys.path.remove(_ROOT)
_sys.path.insert(0, _ROOT)
for _name in list(_sys.modules):
    if _name == "meeting_rooms" or _name.startswith("meeting_rooms."):
        _f = getattr(_sys.modules.get(_name), "__file__", "") or ""
        if not _f.startswith(_ROOT):
            del _sys.modules[_name]
import meeting_rooms as _mr
assert getattr(_mr, "__file__", "").startswith(_ROOT), (
    "stale meeting_rooms shadowed the bundle: %r" % getattr(_mr, "__file__", None)
)
from meeting_rooms import analytics as A

# LABELLED SYNTHETIC FIXTURE (NOT real snapshot rows). Hand-computed expectations
# below. Window dates are INCLUSIVE (Asia/Hong_Kong); booked hours are ACTIVE
# interval intersections clipped to [start_day 00:00, end_day+1 00:00).
FIXTURE = [
 {"room_id":"r1","start_dt":"2026-03-02T09:00:00+08:00","end_dt":"2026-03-02T11:00:00+08:00","status":"active"},
 {"room_id":"r1","start_dt":"2026-03-02T10:00:00+08:00","end_dt":"2026-03-02T12:00:00+08:00","status":"active"},
 {"room_id":"r2","start_dt":"2026-03-03T14:00:00+08:00","end_dt":"2026-03-03T16:00:00+08:00","status":"active"},
 {"room_id":"r2","start_dt":"2026-03-03T15:00:00+08:00","end_dt":"2026-03-03T17:00:00+08:00","status":"cancelled"},
 {"room_id":"r1","start_dt":"2026-03-04T08:00:00+08:00","end_dt":"2026-03-04T09:00:00+08:00","status":"active"},
]
START, END = "2026-03-02", "2026-03-04"
# hand-computed: r1 active=3, hours=5.0 ; r2 active=1, hours=2.0
# cancelled=1 of 5 overlapping -> rate 0.2 ; peak_day 2026-03-02 ; peak_hour 10

def _res(rows=FIXTURE, start=START, end=END):
    return A.compute_analytics(rows, start, end)

def _rooms(res):
    for k in ("rooms", "per_room", "by_room"):
        if isinstance(res.get(k), (list, dict)):
            v = res[k]
            if isinstance(v, dict):
                v = [dict(room_id=k2, **vv) for k2, vv in v.items()]
            return {r.get("room_id"): r for r in v}
    raise AssertionError("no per-room collection in result: %r" % list(res))

def _num(d, *names):
    for n in names:
        if n in d:
            return d[n]
    return d[next(n for n in list(names) + ["active_bookings", "reserved_hours", "cancelled_bookings", "cancelled", "count", "hours"] if n in d)] if any(n in d for n in list(names) + ["active_bookings", "reserved_hours", "cancelled_bookings", "cancelled", "count", "hours"]) else (_ for _ in ()).throw(AssertionError("none of %r in %r" % (names, d)))

class TestDashboardFixture(unittest.TestCase):
    def test_per_room_counts(self):
        rooms = _rooms(_res())
        self.assertEqual(_num(rooms["r1"], "count", "active", "active_count", "bookings"), 3)
        self.assertEqual(_num(rooms["r2"], "count", "active", "active_count", "bookings"), 1)

    def test_per_room_hours(self):
        rooms = _rooms(_res())
        self.assertAlmostEqual(float(_num(rooms["r1"], "hours", "reserved_hours", "booked_hours")), 5.0, places=6)
        self.assertAlmostEqual(float(_num(rooms["r2"], "hours", "reserved_hours", "booked_hours")), 2.0, places=6)

    def test_peak_day_and_hour(self):
        res = _res()
        self.assertEqual(str(_num(res, "peak_day")), "2026-03-02")
        self.assertEqual(int(_num(res, "peak_hour")), 10)

    def test_cancellation_count_and_rate(self):
        res = _res()
        c = res.get("cancellations", res)
        self.assertEqual(int(_num(c if isinstance(c, dict) else res,
                                 "count", "cancelled", "cancellations")), 1)
        self.assertAlmostEqual(float(_num(c if isinstance(c, dict) else res,
                                        "rate", "cancel_rate", "cancellation_rate")), 0.2, places=6)

    def test_date_filter_excludes_outside_window(self):
        # a booking fully outside the window must not be counted
        extra = FIXTURE + [{"room_id":"r1","start_dt":"2026-03-20T09:00:00+08:00",
                            "end_dt":"2026-03-20T10:00:00+08:00","status":"active"}]
        rooms = _rooms(_res(extra))
        self.assertEqual(_num(rooms["r1"], "count", "active", "active_count", "bookings"), 3)

if __name__ == "__main__":
    unittest.main()
