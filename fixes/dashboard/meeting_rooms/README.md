# Meeting Room Booking System

A self-contained, standard-library-only meeting room booking system that replaces
the shared spreadsheet. It prevents double bookings, lets you check room
availability for any date/time range, create bookings, and cancel bookings so the
slot frees up immediately. Data is stored in SQLite and survives restarts.

## Requirements

* Python 3.10+ (standard library only; no third-party packages, no network needed).

## Files

```
meeting_rooms/
  __init__.py         package marker
  domain.py           time parsing, validation, overlap rule
  storage.py          SQLite schema, seeding, booking persistence
  service.py          availability + dashboard business logic
  cli.py              command line interface
  web.py              http.server JSON API + static UI host
  static/
    index.html        web UI markup
    app.js            web UI logic (valid vanilla JS)
  tests/
    test_booking.py   unittest suite
  README.md           this file
```

## Setup

No install step is required. From the directory that contains the `meeting_rooms`
package, run the commands below. The default database file is
`meeting_rooms.sqlite3`; override it with `--db PATH` (CLI) or the
`MEETING_ROOMS_DB` environment variable.

## Run the tests (exact command)

```
python -m unittest discover -v
```

Expected: `Ran 26 tests ... OK` and exit code 0. The suite includes a
concurrency regression test (`test_concurrent_first_use_no_500`) that starts the
real ThreadingHTTPServer on an ephemeral port with a fresh temp DB and fires 24
simultaneous POST /api/bookings for the same slot, asserting only 201/409 status
codes and zero 500s.

## Concurrency notes

The shared BookingStore is created exactly once, before the server starts
serving (`init_store()` is called inside `make_server()` before `serve_forever`).
`get_store()` additionally uses a module-level `threading.Lock` with
double-checked locking as a safe lazy fallback. Schema creation uses
`CREATE TABLE IF NOT EXISTS` inside one transaction and sample rooms are seeded
with `INSERT OR IGNORE` guarded by the `UNIQUE` room name, so concurrent or
repeated first use cannot duplicate seed data. The single sqlite3 connection is
opened with `check_same_thread=False` and all writes are serialised by an inner
`RLock`, so concurrent inserts never raise 

## Run the CLI

```
python -m meeting_rooms.cli --help
python -m meeting_rooms.cli rooms
python -m meeting_rooms.cli availability --date 2026-11-20 --start 10:00 --end 11:00
python -m meeting_rooms.cli book --room 1 --date 2026-11-20 --start 10:00 --end 11:00 --booker Alice --title Standup
python -m meeting_rooms.cli bookings
python -m meeting_rooms.cli cancel --id 1
python -m meeting_rooms.cli dashboard --date 2026-11-20
```

The CLI exits 2 on validation/overlap errors and 3 when a room or booking id is
not found, printing a clear message on stderr.

## Run the web UI

```
python -m meeting_rooms.web --port 8000
```

Then open http://127.0.0.1:8000 in a browser. The page lets you list rooms, check
availability for a date/time range, create a booking (busy rooms are rejected with
a message), and cancel bookings from the bookings table. All sections include an
availability/dashboard count (rooms free vs busy, active vs cancelled bookings).

### JSON API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/rooms` | list rooms with details |
| GET | `/api/availability?date=&start=&end=` | every room free/busy for the range |
| GET | `/api/bookings` | list bookings (add `?active=1` for active only) |
| GET | `/api/dashboard?date=` | counts: rooms, active and cancelled bookings |
| POST | `/api/bookings` | body `{room_id,date,start,end,booker,title}` |
| POST | `/api/bookings/<id>/cancel` | cancel a booking by id |

Overlap returns HTTP 409 with a clear `error` message; invalid input returns 400;
missing room/booking returns 404.

## Rules and assumptions

* **Time model:** one fixed, timezone-unaware local timezone. Times are stored as
  ISO strings `YYYY-MM-DDTHH:MM`, so plain string comparison is a correct order.
* **Overlap rule:** a new booking `[s,e)` is rejected only if it intersects an
  active booking of the same room: `existing.start < new.end AND new.start < existing.end`.
  Back-to-back bookings (`existing.end == new.start`) are **allowed**.
* **Invalid ranges:** zero-length or reversed ranges (`end <= start`) are rejected.
* **Cancellation:** sets status to `cancelled`; cancelled rows are excluded from
  availability and from the active booking count, so the slot is free immediately.
* **Seed data:** four sample rooms are inserted on first run (Room A/B/C, Boardroom).
* **No authentication:** booker name is free text; cancellation is open.
* One-off (non-recurring) bookings only in this version.
