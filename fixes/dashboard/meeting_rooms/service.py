"""Availability and dashboard business logic."""

from __future__ import annotations

from .domain import ValidationError, combine, parse_date, parse_time
from .storage import BookingStore


class BookingService:
    """Service layer coordinating validation and the store."""

    def __init__(self, store: BookingStore):
        self.store = store

    def day_range(self, date_str: str, start_time: str, end_time: str):
        date_str = parse_date(date_str)
        start = combine(date_str, start_time)
        end = combine(date_str, end_time)
        if end <= start:
            raise ValidationError(
                f"End {end} must be after start {start} "
                "(zero-length or reversed ranges are not allowed)"
            )
        return start, end

    def availability(self, date_str: str, start_time: str, end_time: str) -> dict:
        """Return every room with free/busy status for [start, end)."""
        start, end = self.day_range(date_str, start_time, end_time)
        rooms = self.store.list_rooms()
        result = []
        free = busy = 0
        for room in rooms:
            clashes = self.store.active_bookings_for_room(room["id"], start, end)
            status = "busy" if clashes else "free"
            if status == "free":
                free += 1
            else:
                busy += 1
            result.append({
                "room": room,
                "status": status,
                "conflicts": clashes,
            })
        return {
            "date": date_str,
            "start": start,
            "end": end,
            "rooms": result,
            "rooms_free": free,
            "rooms_busy": busy,
        }

    def dashboard(self, date_str: str | None = None) -> dict:
        """Counts: total rooms, active bookings, cancelled bookings."""
        rooms = self.store.list_rooms()
        bookings = self.store.list_bookings(include_cancelled=True)
        active = [b for b in bookings if b["status"] == "active"]
        cancelled = [b for b in bookings if b["status"] == "cancelled"]
        on_date = None
        if date_str:
            on_date = parse_date(date_str)
            active = [b for b in active if b["start_dt"].startswith(on_date)]
        return {
            "date": on_date,
            "total_rooms": len(rooms),
            "active_bookings": len(active),
            "cancelled_bookings": len(cancelled),
        }
