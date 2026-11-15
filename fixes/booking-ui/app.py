"""Single entry point for the meeting-room booking system.

Run:  python3 app.py
Then open http://127.0.0.1:8000 in a browser.

Standard library only. Persists bookings in ./bookings.db (SQLite) so records
survive restarts. All times are HKT (UTC+8), 30-minute granularity, bookable
08:00-20:00, half-open [start, end) overlap semantics (back-to-back allowed).
"""

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from booking_core import (
    BookingStore,
    ValidationError,
    ConflictError,
    NotFoundError,
    validate_interval,
    ROOMS,
    BOOKABLE_START,
    BOOKABLE_END,
    GRANULARITY_MINUTES,
    TIMEZONE_LABEL,
)

DB_PATH = "bookings.db"
HOST = "127.0.0.1"
PORT = 8000

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    room TEXT NOT NULL,
    date TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    status TEXT NOT NULL,
    seq INTEGER NOT NULL
);
"""


class SQLiteBookingStore(BookingStore):
    """BookingStore backed by a SQLite file; loaded on start, written on change."""

    def __init__(self, db_path=DB_PATH, rooms=None):
        self.db_path = db_path
        self._lock = threading.RLock()
        conn = self._connect()
        try:
            conn.execute(SCHEMA)
            conn.commit()
            rows = conn.execute(
                "SELECT id, room, date, start, end, status, seq FROM bookings"
            ).fetchall()
        finally:
            conn.close()
        bookings = [
            {
                "id": r[0], "room": r[1], "date": r[2], "start": r[3],
                "end": r[4], "status": r[5], "seq": r[6],
            }
            for r in rows
        ]
        super().__init__(rooms=rooms, bookings=bookings)

    def _connect(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _persist_book(self, booking):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO bookings "
                    "(id, room, date, start, end, status, seq) VALUES (?,?,?,?,?,?,?)",
                    (
                        booking["id"], booking["room"], booking["date"],
                        booking["start"], booking["end"], booking["status"],
                        booking["seq"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def _persist_cancel(self, booking):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE bookings SET status = ? WHERE id = ?",
                    (booking["status"], booking["id"]),
                )
                conn.commit()
            finally:
                conn.close()

    def book(self, *args, **kwargs):
        with self._lock:
            return super().book(*args, **kwargs)

    def cancel(self, *args, **kwargs):
        with self._lock:
            return super().cancel(*args, **kwargs)


# The store is created in main() (or injected by tests) to avoid an import-time
# side effect that would create a bookings.db file merely by importing app.py.
STORE = None


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Meeting Room Booking</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,Arial,sans-serif;margin:24px;max-width:900px}
h1{font-size:20px} fieldset{margin-bottom:16px} label{display:inline-block;min-width:90px}
input,select{padding:4px;margin:2px 6px 2px 0}
button{padding:5px 10px;cursor:pointer}
.ok{color:#0a0}.err{color:#c00}.muted{color:#666;font-size:12px}
table{border-collapse:collapse;margin-top:8px} th,td{border:1px solid #ccc;padding:4px 8px;font-size:13px}
</style></head><body>
<h1>Meeting Room Booking <span class="muted">(HKT UTC+8, 30-min slots, 08:00-20:00)</span></h1>

<fieldset><legend>Availability check</legend>
<label>Room</label><select id="avRoom"></select>
<label>Date</label><input type="date" id="avDate">
<label>Start</label><input type="time" id="avStart" value="09:00" step="1800">
<label>End</label><input type="time" id="avEnd" value="10:00" step="1800">
<button onclick="checkAvail()">Check</button>
<div id="avResult" class="muted"></div>
</fieldset>

<fieldset><legend>Book a room</legend>
<label>Room</label><select id="bkRoom"></select>
<label>Date</label><input type="date" id="bkDate">
<label>Start</label><input type="time" id="bkStart" value="09:00" step="1800">
<label>End</label><input type="time" id="bkEnd" value="10:00" step="1800">
<button onclick="createBooking()">Book</button>
<div id="bkResult" class="muted"></div>
</fieldset>

<fieldset><legend>Bookings</legend>
<label>Date filter</label><input type="date" id="lsDate">
<button onclick="loadBookings()">Refresh</button>
<div id="bkList"></div>
</fieldset>

<script>
function esc(s){return String(s).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
async function api(path){const r=await fetch(path);return await r.json();}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return await r.json();}
async function init(){
  const data=await api('/api/rooms');
  const opts=data.rooms.map(function(r){return '<option>'+esc(r)+'</option>';}).join('');
  document.getElementById('avRoom').innerHTML=opts;
  document.getElementById('bkRoom').innerHTML=opts;
  const today=new Date().toISOString().slice(0,10);
  ['avDate','bkDate','lsDate'].forEach(function(id){document.getElementById(id).value=today;});
  loadBookings();
}
async function checkAvail(){
  const el=document.getElementById('avResult');
  const q=new URLSearchParams({room:document.getElementById('avRoom').value,date:document.getElementById('avDate').value,start:document.getElementById('avStart').value,end:document.getElementById('avEnd').value});
  const data=await api('/api/availability?'+q.toString());
  if(data.error){el.className='err';el.textContent=data.error;return;}
  if(data.available){el.className='ok';el.textContent='Available';}
  else{el.className='err';el.textContent='BUSY - '+data.conflicts.length+' conflicting booking(s)';}
}
async function createBooking(){
  const el=document.getElementById('bkResult');
  const data=await post('/api/book',{room:document.getElementById('bkRoom').value,date:document.getElementById('bkDate').value,start:document.getElementById('bkStart').value,end:document.getElementById('bkEnd').value});
  if(data.error){el.className='err';el.textContent=data.error;return;}
  el.className='ok';el.textContent='Booked: '+data.booking.id;
  loadBookings();
}
async function cancelBooking(id){
  const data=await post('/api/cancel',{id:id});
  if(data.error){alert(data.error);return;}
  loadBookings();
}
async function loadBookings(){
  const d=document.getElementById('lsDate').value;
  const q=d?('?date='+encodeURIComponent(d)):'';
  const data=await api('/api/bookings'+q);
  const rows=data.bookings.map(function(b){return '<tr><td>'+esc(b.room)+'</td><td>'+esc(b.date)+'</td><td>'+esc(b.start)+'-'+esc(b.end)+'</td><td><code>'+esc(b.id).slice(0,10)+'</code></td><td><button onclick="cancelBooking(\''+b.id+'\')">Cancel</button></td></tr>';}).join('');
  document.getElementById('bkList').innerHTML='<table><tr><th>Room</th><th>Date</th><th>Time</th><th>ID</th><th></th></tr>'+(rows||'<tr><td colspan=5>No bookings</td></tr>')+'</table>';
}
init();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def log_message(self, fmt, *args):
        pass  # keep console quiet (default handler logs every request)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, ctype="text/html; charset=utf-8")
            return
        if parsed.path == "/api/rooms":
            self._json(200, {"rooms": STORE.rooms})
            return
        if parsed.path == "/api/bookings":
            date = q.get("date", [None])[0] or None
            self._json(200, {"bookings": STORE.list_bookings(date=date)})
            return
        if parsed.path == "/api/availability":
            room = q.get("room", [""])[0]
            date = q.get("date", [""])[0]
            start = q.get("start", [""])[0]
            end = q.get("end", [""])[0]
            try:
                if room not in STORE.rooms:
                    raise ValidationError("unknown room: %r" % room)
                norm_date, norm_start, norm_end = validate_interval(date, start, end)
            except ValidationError as exc:
                self._json(400, {"error": str(exc)})
                return
            conflicts = STORE.find_conflicts(room, norm_date, norm_start, norm_end)
            self._json(200, {"available": not conflicts, "conflicts": conflicts})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid JSON body"})
            return
        if parsed.path == "/api/book":
            try:
                booking = STORE.book(
                    payload.get("room", ""),
                    payload.get("date", ""),
                    payload.get("start", ""),
                    payload.get("end", ""),
                )
            except ConflictError as exc:
                self._json(409, {"error": "Conflict: %s" % exc})
                return
            except (ValidationError, NotFoundError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(201, {"booking": booking})
            return
        if parsed.path == "/api/cancel":
            try:
                booking = STORE.cancel(payload.get("id", ""))
            except NotFoundError as exc:
                self._json(404, {"error": str(exc)})
                return
            except ValidationError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"cancelled": booking})
            return
        self._json(404, {"error": "not found"})


def main():
    global STORE
    STORE = SQLiteBookingStore(DB_PATH)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Meeting room booking system running at http://%s:%d" % (HOST, PORT))
    print("Rooms: %s" % ", ".join(ROOMS))
    print("Timezone %s | slots %d min | window %s-%s"
          % (TIMEZONE_LABEL, GRANULARITY_MINUTES, BOOKABLE_START, BOOKABLE_END))
    print("Persistence: %s" % DB_PATH)
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
