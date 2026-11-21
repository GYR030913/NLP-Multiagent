import unittest
from meeting_rooms.analytics import compute_analytics
class Boundaries(unittest.TestCase):
 def test_clipping_boundaries_cancellation_and_zero_room(self):
  rows=[dict(room_id=1,start_dt="2026-03-01T23:00:00",end_dt="2026-03-02T01:30:00",status="active"),dict(room_id=1,start_dt="2026-03-02T23:00:00+08:00",end_dt="2026-03-03T02:00:00+08:00",status="active"),dict(room_id=1,start_dt="2026-03-02T02:00:00",end_dt="2026-03-02T03:00:00",status="cancelled"),dict(room_id=1,start_dt="2026-03-01T23:00:00",end_dt="2026-03-02T00:00:00",status="active")]
  r=compute_analytics(rows,"2026-03-02","2026-03-02",rooms=[dict(id=1,name="A"),dict(id=2,name="B")])
  self.assertEqual([(x["count"],x["hours"]) for x in r["rooms"]],[(2,2.5),(0,0)])
  self.assertEqual(r["cancellations"],dict(count=1,denominator=3,rate=1/3))
 def test_empty_and_invalid(self):
  r=compute_analytics([],"2026-03-02","2026-03-02")
  self.assertIsNone(r["peak_hour"]);self.assertEqual(r["cancellations"]["rate"],0)
  with self.assertRaises(ValueError):compute_analytics([],"2026-03-03","2026-03-02")
 def test_equivalent_utc_and_hong_kong_times(self):
  r=compute_analytics([dict(room_id=1,start_dt="2026-03-02T01:00:00+00:00",end_dt="2026-03-02T03:00:00+00:00",status="active")],"2026-03-02","2026-03-02")
  self.assertEqual(r["rooms"][0]["hours"],2);self.assertEqual(r["peak_hour"],9)
