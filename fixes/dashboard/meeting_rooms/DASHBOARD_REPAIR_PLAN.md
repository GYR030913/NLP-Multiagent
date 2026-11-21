# Dashboard repair plan (evidence-attributed draft)

Task: t17 (run r3). Status: PARTIAL - inspection/code.run temporarily disabled by an
active output checkpoint, so this increment is a written repair plan grounded only in
already-observed evidence. No test is claimed to pass by this document.

## Observed defect (event seq 1408, code.run exit 1, cwd work/b4)
Running `python -m unittest` on the b4 workspace produced failures showing a contract
mismatch between the analytics implementation and `meeting_rooms/tests/test_dashboard.py`:

- Per-room object actually emitted contains keys:
  `{'room_id': 'r1', 'room': 'r1', 'active_bookings': 3, 'reserved_hours': 5.0}`
  The test helper `_num(...)` searched for
  `('count','active','active_count','bookings')` and therefore raised
  `AssertionError: none of (...) in {...}` (test_dashboard.py line 51 / 82).
- `peak_day` is emitted as a dict `{'date': '2026-03-02', 'count': 2}`, but
  `test_peak_day_and_hour` asserted `str(_num(res,'peak_day')) == '2026-03-02'`.

Interpretation (uncertain): the implementation key names differ from the test's
assumed key names; one of the two must be aligned. The analytics module is the
source of truth consumed by the web endpoint/JS, so prefer adapting the TEST
helper to the real keys, plus (optionally) emitting aliases for readability.

## Exact, minimal fixes to apply when editing resumes
1. In `meeting_rooms/tests/test_dashboard.py`, extend the candidate-name tuple used
   by `_num` to include the real keys:
   add `'active_bookings'` and `'reserved_hours'` (and keep the old aliases).
   - Replace: the call `_num(rooms["r1"], "count", "active", "active_count", "bookings")`
     is the observed r1 call; ensure every per-room `_num` call uses the aliases.
2. Make `_num` tolerate dict-valued fields: if the found value is a dict containing
   `'date'` or `'hour'`, return that inner scalar. This fixes the peak_day/peak_hour
   assertions without changing the implementation output shape.
3. Re-run the full suite and confirm: per-room active count, per-room reserved hours,
   peak day, peak hour, cancellation count+rate, inclusive-window exclusion test, and
   the pre-existing booking/overlap-409/cancellation suite (room-name display) all pass.

## Missing evidence / blockers
- BLOCKER: current turn has only `files.write/append/replace` + memory/evidence/publish
  tools; `files.read` and `code.run` are unavailable, so the exact current text of
  `_num` and of `analytics.py`'s room-dict emission could NOT be re-read this turn.
  Exact-match replacements therefore cannot be applied safely yet.
- MISSING: string value of `peak_hour` (assumed `{'hour':..,'count':..}` or int); and the
  cancellation field names. Must be read from `analytics.py` before writing assertions.
- MISSING: the real snapshot is ds1 (`bookings_snapshot`, sha256 38f17d88...), 5 rows,
  timezone Asia/Hong_Kong, as_of 2026-11-21; a real-data render check is still outstanding.

## Next action
When inspection tools return, read `meeting_rooms/tests/test_dashboard.py` and
`meeting_rooms/analytics.py`, apply fixes 1-2 with exact snippets, then run the suite
(fix 3). Do NOT publish the bundle until the suite is green and the UI selector works.
