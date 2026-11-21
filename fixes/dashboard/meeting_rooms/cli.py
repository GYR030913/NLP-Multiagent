"""Command line interface for the meeting room booking system.

Examples:
    python -m meeting_rooms.cli --help
    python -m meeting_rooms.cli rooms
    python -m meeting_rooms.cli availability --date 2026-11-20 --start 10:00 --end 11:00
    python -m meeting_rooms.cli book --room 1 --date 2026-11-20 --start 10:00 --end 11:00 --booker Alice
    python -m meeting_rooms.cli bookings
    python -m meeting_rooms.cli cancel --id 1
"""

from __future__ import annotations

import argparse
import json
import sys

from .domain import NotFoundError, OverlapError, ValidationError
from .service import BookingService
from .storage import DEFAULT_DB, BookingStore


def _print(obj) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meeting_rooms.cli",
        description="Meeting room booking CLI (standard library only).",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help="Path to the SQLite database file (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("rooms", help="List all rooms with details")

    avail = sub.add_parser("availability", help="Show free/busy per room")
    avail.add_argument("--date", required=True)
    avail.add_argument("--start", required=True)
    avail.add_argument("--end", required=True)

    book = sub.add_parser("book", help="Create a booking")
    book.add_argument("--room", required=True, type=int)
    book.add_argument("--date", required=True)
    book.add_argument("--start", required=True)
    book.add_argument("--end", required=True)
    book.add_argument("--booker", default="")
    book.add_argument("--title", default="")

    bookings = sub.add_parser("bookings", help="List bookings")
    bookings.add_argument("--active-only", action="store_true")

    cancel = sub.add_parser("cancel", help="Cancel a booking by id")
    cancel.add_argument("--id", required=True, type=int)

    dash = sub.add_parser("dashboard", help="Availability/booking counts")
    dash.add_argument("--date", default=None)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = BookingStore(args.db)
    service = BookingService(store)
    try:
        if args.command == "rooms":
            _print(store.list_rooms())
        elif args.command == "availability":
            _print(service.availability(args.date, args.start, args.end))
        elif args.command == "book":
            start = f"{args.date}T{args.start}"
            end = f"{args.date}T{args.end}"
            booking = store.create_booking(
                args.room, start, end, args.booker, args.title
            )
            print(
                f"BOOKED #{booking['id']} room {booking['room_id']} "
                f"{booking['start_dt']} -> {booking['end_dt']} "
                f"status={booking['status']}"
            )
            _print(booking)
        elif args.command == "bookings":
            _print(store.list_bookings(include_cancelled=not args.active_only))
        elif args.command == "cancel":
            booking = store.cancel_booking(args.id)
            print(
                f"CANCELLED #{booking['id']} status={booking['status']}"
            )
            _print(booking)
        elif args.command == "dashboard":
            _print(service.dashboard(args.date))
        else:  # pragma: no cover - argparse enforces choices
            parser.error(f"unknown command {args.command!r}")
        return 0
    except (ValidationError, OverlapError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except NotFoundError as exc:
        print(f"NOT FOUND: {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
