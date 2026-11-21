"""SQLite persistence layer for rooms and bookings.

Thread safety: the web server uses ThreadingHTTPServer, so several request
threads may touch the shared store concurrently. The single sqlite3 connection
is therefore opened with check_same_thread=False and every public method is
serialised with an internal RLock. SQLite still guarantees durability on
commit, so a booking written by one process is visible to the next.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from .domain import (
    NotFoundError,
    OverlapError,
    validate_range,
)

DEFAULT_DB = os.environ.get("MEETING_ROOMS_DB", "meeting_rooms.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    capacity INTEGER NOT NULL,
    location TEXT NOT NULL,
    equipment TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    start_dt TEXT NOT NULL,
    end_dt TEXT NOT NULL,
    booker TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
"""

SAMPLE_ROOMS = [
    ("Room A", 6, "1/F East Wing", "TV, whiteboard"),
    ("Room B", 12, "1/F West Wing", "Projector, video conference"),
    ("Room C", 4, "2/F North", "Whiteboard"),
    ("Boardroom", 20, "3/F Executive", "Display, conference mic, catering"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class BookingStore:
    """Thin, thread-safe wrapper around a SQLite database of rooms/bookings."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
            self.conn.execute("PRAGMA foreign_keys = ON")
            self._seed_rooms()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _seed_rooms(self) -> None:
        # Idempotent, race-safe seeding. rooms.name is UNIQUE, so
        # INSERT OR IGNORE makes repeated or concurrent seeding safe: a
        # second starter cannot duplicate rooms and leaves the seeded set
        # intact. Schema creation (executescript of CREATE TABLE IF NOT
        # EXISTS) runs inside the same __init__ transaction/lock.
        self.conn.executemany(
            "INSERT OR IGNORE INTO rooms (name, capacity, location, equipment) "
            "VALUES (?, ?, ?, ?)",
            SAMPLE_ROOMS,
        )
        self.conn.commit()

    # -- rooms -----------------------------------------------------------
    def list_rooms(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, name, capacity, location, equipment "
                "FROM rooms ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_room(self, room_id: int) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT id, name, capacity, location, equipment "
                "FROM rooms WHERE id = ?",
                (room_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Room id {room_id} does not exist")
            return dict(row)

    # -- bookings --------------------------------------------------------
    def list_bookings(self, include_cancelled: bool = True) -> list[dict]:
        sql = "SELECT * FROM bookings"
        if not include_cancelled:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY start_dt, id"
        with self._lock:
            rows = self.conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_booking(self, booking_id: int) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM bookings WHERE id = ?", (booking_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Booking id {booking_id} does not exist")
            return dict(row)

    def active_bookings_for_room(
        self, room_id: int, start_dt: str, end_dt: str
    ) -> list[dict]:
        """Active bookings for a room whose range may intersect [s,e)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM bookings WHERE room_id = ? AND status = 'active' "
                "AND start_dt < ? AND ? < end_dt",
                (room_id, end_dt, start_dt),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_booking(
        self,
        room_id: int,
        start_dt: str,
        end_dt: str,
        booker: str = "",
        title: str = "",
    ) -> dict:
        """Create a booking, rejecting overlaps of active bookings.

        Overlap rule: existing.start < new.end AND new.start < existing.end.
        Adjacent (existing.end == new.start) is allowed. A zero-length or
        reversed range (end <= start) is rejected by validate_range.
        """
        start_dt, end_dt = validate_range(start_dt, end_dt)
        with self._lock:
            room = self.get_room(room_id)
            clashes = self.active_bookings_for_room(room_id, start_dt, end_dt)
            if clashes:
                first = clashes[0]
                raise OverlapError(
                    f"Room {room['name']} is already booked "
                    f"{first['start_dt']} to {first['end_dt']} "
                    f"(booking #{first['id']}), which overlaps "
                    f"{start_dt} to {end_dt}. Double booking rejected."
                )
            cur = self.conn.execute(
                "INSERT INTO bookings "
                "(room_id, start_dt, end_dt, booker, title, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?)",
                (room_id, start_dt, end_dt, booker, title, _utc_now()),
            )
            self.conn.commit()
            return self.get_booking(cur.lastrowid)

    def cancel_booking(self, booking_id: int) -> dict:
        with self._lock:
            booking = self.get_booking(booking_id)
            if booking["status"] == "cancelled":
                return booking
            self.conn.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE id = ?",
                (booking_id,),
            )
            self.conn.commit()
            return self.get_booking(booking_id)
