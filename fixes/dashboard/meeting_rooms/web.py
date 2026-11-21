"""Web UI and JSON API for the meeting room booking system.

Run with:  python -m meeting_rooms.web  [--port 8000] [--db path]
Serves a static HTML+JS page and JSON endpoints:
  GET  /api/rooms
  GET  /api/availability?date=&start=&end=
  GET  /api/bookings
  GET  /api/dashboard?start=&end=  (inclusive Asia/Hong_Kong window; ?date= kept for back-compat)
  POST /api/bookings        {room_id,date,start,end,booker,title}
  POST /api/bookings/<id>/cancel
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .domain import NotFoundError, OverlapError, ValidationError, combine
from .service import BookingService
from .storage import DEFAULT_DB, BookingStore

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

DB_PATH = DEFAULT_DB

# Module-level guard for one-time, thread-safe store construction. Request
# threads may call get_store() concurrently (ThreadingHTTPServer), so the
# lazy singleton below uses double-checked locking; in normal operation the
# store is created once in make_server() before serve_forever, so no request
# thread ever constructs it.
_STORE: BookingStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> BookingStore:
    """Return the process-wide store, constructing it at most once.

    Double-checked locking: the fast path avoids taking the lock once the
    store exists; the slow path serialises first-use construction so two
    threads cannot race to initialise the schema/seed data.
    """
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = BookingStore(DB_PATH)
    return _STORE


def init_store(db_path: str | None = None) -> BookingStore:
    """Eagerly create the shared store (called before serving).

    This makes first use atomic: the schema and sample rooms are created
    once, before any request thread can call get_store().
    """
    global DB_PATH, _STORE
    with _STORE_LOCK:
        if db_path:
            DB_PATH = db_path
        if _STORE is None:
            _STORE = BookingStore(DB_PATH)
    return _STORE


def _json(handler, status, payload):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("JSON body must be an object")
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "MeetingRooms/1.0"

    def log_message(self, fmt, *args):
        pass  # keep test output quiet

    # -- static ----------------------------------------------------------
    def _serve_static(self, path: str) -> bool:
        if path in ("/", "/index.html"):
            name = "index.html"
        elif path in ("/app.js", "/dashboard.js"):
            name = path[1:]
        else:
            return False
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            return False
        ctype = "text/html; charset=utf-8" if name.endswith(".html") \
            else "application/javascript; charset=utf-8"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    # -- GET -------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/api/rooms":
                _json(self, 200, {"rooms": get_store().list_rooms()})
                return
            if path == "/api/bookings":
                active = query.get("active", ["0"])[0] in ("1", "true", "yes")
                items = get_store().list_bookings(include_cancelled=not active)
                _json(self, 200, {"bookings": items})
                return
            if path == "/api/dashboard":
                start = query.get("start", [None])[0]
                end = query.get("end", [None])[0]
                if start is not None or end is not None:
                    from .analytics import compute_analytics
                    try:
                        result = compute_analytics(get_store().list_bookings(include_cancelled=True), start, end, rooms=get_store().list_rooms())
                    except (ValueError, TypeError) as exc:
                        raise ValidationError(str(exc)) from exc
                    _json(self, 200, result)
                else:
                    date = query.get("date", [None])[0]
                    _json(self, 200, BookingService(get_store()).dashboard(date))
                return

            if path == "/api/availability":
                missing = [k for k in ("date", "start", "end") if k not in query]
                if missing:
                    _json(self, 400, {"error": "Missing params: " + ", ".join(missing)})
                    return
                svc = BookingService(get_store())
                svc_res = svc.availability(
                    query["date"][0], query["start"][0], query["end"][0]
                )
                _json(self, 200, svc_res)
                return
            if self._serve_static(parsed.path):
                return
            _json(self, 404, {"error": f"Not found: {parsed.path}"})
        except ValidationError as exc:
            _json(self, 400, {"error": str(exc)})
        except NotFoundError as exc:
            _json(self, 404, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            _json(self, 500, {"error": str(exc)})

    # -- POST ------------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            if path == "/api/bookings":
                data = _read_json(self)
                date = data.get("date", "")
                start = data.get("start", "")
                end = data.get("end", "")
                try:
                    room_id = int(data.get("room_id"))
                except (TypeError, ValueError):
                    raise ValidationError("room_id must be an integer")
                s = combine(date, start)
                e = combine(date, end)
                booking = get_store().create_booking(
                    room_id, s, e,
                    data.get("booker", ""), data.get("title", ""),
                )
                _json(self, 201, {"booking": booking, "message": "Booked successfully"})
                return
            if path.startswith("/api/bookings/") and path.endswith("/cancel"):
                parts = path.split("/")
                try:
                    bid = int(parts[3])
                except (IndexError, ValueError):
                    raise ValidationError("Invalid booking id in path")
                booking = get_store().cancel_booking(bid)
                _json(self, 200, {"booking": booking, "message": "Booking cancelled"})
                return
            _json(self, 404, {"error": f"Not found: {parsed.path}"})
        except ValidationError as exc:
            _json(self, 400, {"error": str(exc)})
        except OverlapError as exc:
            _json(self, 409, {"error": str(exc)})
        except NotFoundError as exc:
            _json(self, 404, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            _json(self, 500, {"error": str(exc)})


def make_server(host="127.0.0.1", port=8000, db_path=None):
    """Create the HTTP server after eagerly initialising the shared store.

    The store is constructed exactly once here, before serve_forever(), so
    no request thread ever races to build the schema/seed data (first-use
    atomicity). get_store() remains a safe lazy fallback for programmatic
    use, but the server path never triggers it.
    """
    init_store(db_path)
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="meeting_rooms.web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args(argv)
    httpd = make_server(args.host, args.port, args.db)
    print(f"Meeting room booking UI on http://{args.host}:{args.port}  (db={args.db})")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
