import unittest,tempfile,threading,json,urllib.request,urllib.error
from pathlib import Path
from meeting_rooms import web
from meeting_rooms.smoke_server import seed
@unittest.skipUnless((Path(__file__).parents[1]/"snapshot.json").exists(), "Observed snapshot is local-only")
class SnapshotAPI(unittest.TestCase):
 def test_observed_snapshot_and_invalid_ranges(self):
  with tempfile.TemporaryDirectory() as folder:
   path=str(Path(folder)/"api.sqlite3");data=seed(path);server=web.make_server(port=0,db_path=path)
   worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
   base="http://127.0.0.1:"+str(server.server_port)
   try:
    with urllib.request.urlopen(base+"/api/dashboard?start="+data["start"]+"&end="+data["end"]) as response:r=json.load(response)
    with urllib.request.urlopen(base+"/dashboard.js") as response:
     self.assertIn("javascript",response.headers["Content-Type"]);self.assertIn("range-start",response.read().decode())
    expected=data["expected_full_range"]
    self.assertEqual(sum(x["count"] for x in r["rooms"]),expected["active"])
    self.assertAlmostEqual(r["total_reserved_hours"],expected["hours"],places=5)
    self.assertEqual(r["cancellations"]["denominator"],expected["records"])
    self.assertEqual(r["cancellations"]["count"],expected["cancelled"])
    self.assertEqual(len(r["rooms"]),len(data["rooms"]))
    for query in ("start=2026-03-03&end=2026-03-02","start=bad&end=bad","start=2026-03-02"):
     with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+"/api/dashboard?"+query)
     self.assertEqual(error.exception.code,400)
   finally:server.shutdown();server.server_close();worker.join(2)
