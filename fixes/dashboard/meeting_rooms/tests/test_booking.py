"""Automated tests for the meeting room booking system.

Covers: overlap boundaries (exact-adjacent allowed, 1-minute overlap
rejected, containment, identical ranges, zero-length), cancellation
consistency, date-range handling, and availability/dashboard math.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from meeting_rooms.domain import (
    OverlapError,
    ValidationError,
    overlaps,
)
from meeting_rooms.service import BookingService
from meeting_rooms.storage import BookingStore

D = "2026-11-20"


def dt(t: str) -> str:
    return f"{D}T{t}"


class TempStoreMixin:
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.store = BookingStore(self.db_path)
        self.service = BookingService(self.store)

    def tearDown(self):
        self.store.close()
        os.unlink(self.db_path)


class TestDomainOverlap(unittest.TestCase):
    def test_adjacent_not_overlapping(self):
        self.assertFalse(overlaps(dt("10:00"), dt("11:00"), dt("11:00"), dt("12:00")))
        self.assertFalse(overlaps(dt("11:00"), dt("12:00"), dt("10:00"), dt("11:00")))

    def test_one_minute_overlap(self):
        self.assertTrue(overlaps(dt("10:00"), dt("11:00"), dt("10:59"), dt("11:59")))
        self.assertTrue(overlaps(dt("10:59"), dt("11:01"), dt("10:00"), dt("11:00")))

    def test_containment_overlapping(self):
        self.assertTrue(overlaps(dt("10:00"), dt("12:00"), dt("10:30"), dt("11:00")))
        self.assertTrue(overlaps(dt("10:30"), dt("11:00"), dt("10:00"), dt("12:00")))

    def test_identical_overlapping(self):
        self.assertTrue(overlaps(dt("10:00"), dt("11:00"), dt("10:00"), dt("11:00")))

    def test_disjoint_not_overlapping(self):
        self.assertFalse(overlaps(dt("09:00"), dt("10:00"), dt("11:00"), dt("12:00")))


class TestBookingRules(TempStoreMixin, unittest.TestCase):
    def test_adjacent_allowed(self):
        b1 = self.store.create_booking(1, dt("10:00"), dt("11:00"))
        b2 = self.store.create_booking(1, dt("11:00"), dt("12:00"))
        self.assertEqual(b1["status"], "active")
        self.assertEqual(b2["status"], "active")

    def test_one_minute_overlap_rejected(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        with self.assertRaises(OverlapError) as ctx:
            self.store.create_booking(1, dt("10:59"), dt("11:01"))
        self.assertIn("overlap", str(ctx.exception).lower())

    def test_containment_rejected(self):
        self.store.create_booking(1, dt("10:00"), dt("12:00"))
        with self.assertRaises(OverlapError):
            self.store.create_booking(1, dt("10:30"), dt("11:00"))

    def test_identical_rejected(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        with self.assertRaises(OverlapError) as ctx:
            self.store.create_booking(1, dt("10:00"), dt("11:00"))
        self.assertIn("Double booking rejected", str(ctx.exception))

    def test_zero_length_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.create_booking(1, dt("10:00"), dt("10:00"))

    def test_reversed_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.create_booking(1, dt("11:00"), dt("10:00"))

    def test_different_room_not_conflicting(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        b = self.store.create_booking(2, dt("10:00"), dt("11:00"))
        self.assertEqual(b["status"], "active")


class TestCancellation(TempStoreMixin, unittest.TestCase):
    def test_cancel_frees_slot_and_excludes_from_active(self):
        b = self.store.create_booking(1, dt("10:00"), dt("11:00"))
        self.assertEqual(len(self.store.active_bookings_for_room(1, dt("10:00"), dt("11:00"))), 1)
        self.store.cancel_booking(b["id"])
        self.assertEqual(self.store.active_bookings_for_room(1, dt("10:00"), dt("11:00")), [])
        avail = self.service.availability(D, "10:00", "11:00")
        room1 = [r for r in avail["rooms"] if r["room"]["id"] == 1][0]
        self.assertEqual(room1["status"], "free")
        dash = self.service.dashboard(D)
        self.assertEqual(dash["active_bookings"], 0)
        self.assertEqual(dash["cancelled_bookings"], 1)

    def test_rebook_after_cancel_allowed(self):
        b = self.store.create_booking(1, dt("10:00"), dt("11:00"))
        self.store.cancel_booking(b["id"])
        again = self.store.create_booking(1, dt("10:00"), dt("11:00"))
        self.assertEqual(again["status"], "active")

    def test_cancel_idempotent(self):
        b = self.store.create_booking(1, dt("10:00"), dt("11:00"))
        first = self.store.cancel_booking(b["id"])
        second = self.store.cancel_booking(b["id"])
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(second["status"], "cancelled")


class TestDateRange(TempStoreMixin, unittest.TestCase):
    def test_invalid_date_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.availability("2026-13-01", "10:00", "11:00")

    def test_invalid_time_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.availability(D, "25:00", "26:00")

    def test_reversed_range_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.availability(D, "11:00", "10:00")

    def test_only_same_date_counts(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        other = self.service.availability("2026-11-21", "10:00", "11:00")
        for entry in other["rooms"]:
            self.assertEqual(entry["status"], "free")
        self.assertEqual(other["date"], "2026-11-21")


class TestAvailabilityDashboard(TempStoreMixin, unittest.TestCase):
    def test_availability_lists_all_rooms(self):
        total = len(self.store.list_rooms())
        avail = self.service.availability(D, "10:00", "11:00")
        self.assertEqual(len(avail["rooms"]), total)
        self.assertEqual(avail["rooms_free"], total)
        self.assertEqual(avail["rooms_busy"], 0)

    def test_availability_marks_busy(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        avail = self.service.availability(D, "10:30", "10:45")
        self.assertEqual(avail["rooms_busy"], 1)
        self.assertEqual(avail["rooms_free"], len(self.store.list_rooms()) - 1)

    def test_dashboard_counts(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        b2 = self.store.create_booking(2, dt("13:00"), dt("14:00"))
        self.store.cancel_booking(b2["id"])
        dash = self.service.dashboard(D)
        self.assertEqual(dash["total_rooms"], len(self.store.list_rooms()))
        self.assertEqual(dash["active_bookings"], 1)
        self.assertEqual(dash["cancelled_bookings"], 1)

    def test_dashboard_date_filter(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        self.assertEqual(self.service.dashboard(D)["active_bookings"], 1)
        self.assertEqual(self.service.dashboard("2026-11-25")["active_bookings"], 0)

    def test_adjacent_bookings_summary(self):
        self.store.create_booking(1, dt("10:00"), dt("11:00"))
        self.store.create_booking(1, dt("11:00"), dt("12:00"))
        avail = self.service.availability(D, "10:00", "12:00")
        room1 = [r for r in avail["rooms"] if r["room"]["id"] == 1][0]
        self.assertEqual(room1["status"], "busy")
        self.assertEqual(len(room1["conflicts"]), 2)


class TestPersistence(unittest.TestCase):
    def test_booking_survives_reopen(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        try:
            s1 = BookingStore(path)
            b = s1.create_booking(1, dt("10:00"), dt("11:00"))
            s1.close()
            s2 = BookingStore(path)
            found = s2.get_booking(b["id"])
            self.assertEqual(found["start_dt"], dt("10:00"))
            self.assertEqual(found["status"], "active")
            self.assertEqual(len(s2.list_rooms()), 4)
            s2.close()
        finally:
            os.unlink(path)


class TestConcurrentFirstUse(unittest.TestCase):
    """Regression for the first-use race: 20+ simultaneous POSTs on a fresh DB.

    The original defect: web.py built the shared BookingStore lazily inside
    request threads (unsynchronised module global), so concurrent first-use
    POSTs raced during schema creation/seeding and returned HTTP 500s. The
    fix (module lock + double-checked get_store() + eager init_store() in
    make_server) must make every response a 201 or 409 and never a 500.
    """

    N = 24

    def test_concurrent_first_use_no_500(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        from meeting_rooms import web as web_module

        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(path)  # guarantee a brand-new, uninitialised DB file
        web_module._STORE = None  # force first-use construction via the server
        httpd = web_module.make_server("127.0.0.1", 0, path)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        statuses = []
        lock = threading.Lock()
        barrier = threading.Barrier(self.N)

        def worker():
            payload = json.dumps({
                "room_id": 1, "date": D, "start": "10:00", "end": "11:00",
                "booker": "race", "title": "race",
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/bookings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            barrier.wait()  # maximise simultaneity
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            except Exception:  # pragma: no cover - connection-level failure
                code = -1
            with lock:
                statuses.append(code)

        try:
            threads = [threading.Thread(target=worker) for _ in range(self.N)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=60)
        finally:
            httpd.shutdown()
            httpd.server_close()
            web_module._STORE = None
            if os.path.exists(path):
                os.unlink(path)

        self.assertEqual(len(statuses), self.N, f"lost responses: {statuses}")
        self.assertEqual([s for s in statuses if s == 500], [],
                         f"unexpected 500s under concurrency: {statuses}")
        self.assertTrue(set(statuses) <= {201, 409},
                        f"unexpected status codes: {sorted(set(statuses))}")
        self.assertEqual(statuses.count(201), 1,
                         f"identical-slot burst must yield exactly one winner: {statuses}")


if __name__ == "__main__":
    unittest.main()
