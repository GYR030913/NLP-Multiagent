"""Read a sanitized observed snapshot; serve only a fresh temporary database."""
import json,tempfile
from pathlib import Path
from .storage import BookingStore
from . import web
def seed(path):
 data=json.loads(Path(__file__).with_name("snapshot.json").read_text())
 store=BookingStore(path)
 for r in data["bookings"]:
  store.conn.execute("INSERT INTO bookings(id,room_id,start_dt,end_dt,status,created_at) VALUES(?,?,?,?,?,?)",tuple(r[k] for k in ("id","room_id","start_dt","end_dt","status","created_at")))
 store.conn.commit();store.close();return data
if __name__=="__main__":
 with tempfile.TemporaryDirectory() as folder:
  path=str(Path(folder)/"snapshot.sqlite3");seed(path);web.main(["--db",path])
