# Software Task 2 engineering repair candidate

This is an explicitly human/engineering-assisted repair, based on the accepted b4 application and the failed r3 workspace. It is not evidence of autonomous task completion, independent agent review, or human acceptance. The old r3 and its 360-action budget remain unchanged.

From this directory, run `python -m meeting_rooms.web --db meeting_rooms.sqlite3`, then open http://127.0.0.1:8000. This creates a separate database; to use existing records, pass an authorized COPY with --db. Do not point experimental code at the original database.

Run `python -m unittest discover -s meeting_rooms/tests -v`. Agent-side verification runs these commands in the offline Docker sandbox. The observed snapshot JSON is intentionally ignored by Git; its API test explicitly skips when the local snapshot is absent. Ordinary application startup does not require that snapshot. `meeting_rooms.smoke_server` serves a new temporary database from the local sanitized snapshot for browser verification only.

## Fixed contract

GET /api/dashboard?start=YYYY-MM-DD&end=YYYY-MM-DD uses inclusive Hong Kong dates. Existing ?date= and no-range behavior remain available for the booking UI. Responses contain rooms[{room_id,name,count,hours}], scalar peak_day/peak_hour (null for empty data), cancellations{count,denominator,rate}, total_reserved_hours and window. Count and hours exclude cancelled rows; duration is clipped to the window. Cancellation denominator includes all overlapping active/cancelled records. Empty rooms remain visible. Peak day counts overlapping active records per calendar day; peak hour counts active record/hour overlaps aggregated over the range. Ties use the earliest date/hour. Naive timestamps mean Hong Kong local time; aware timestamps are converted. Reversed, incomplete or over-3661-day ranges return HTTP 400.

## Validation on 2026-11-22

35 Docker tests passed, including 26 original booking tests, 5 existing dashboard fixture tests, 3 new boundary tests and 1 observed-snapshot API test. SQLite independently recomputed 5 records, 2 active, 3 cancelled, 2 reserved hours and 60% cancellations. Real browser validation passed all 20 steps; the initial dashboard.js 404 failure is retained separately. Evidence is local under runtime/task2_validation_20261122, including unit-api.json, browser.json and engineering-bundle.json. No external model calls or new approvals were fabricated. The sanitized snapshot is local-only and not committed.

## Improvements applied and remaining

A single implementation replaces stacked compatibility wrappers; API, tests and UI share an explicit contract. Range API and UI are actually connected, dashboard.js is explicitly served, rendering uses textContent, and stale asynchronous responses are ignored. Validation combines independent arithmetic, API checks and browser interaction.

The general harness still needs to enforce agreed output contracts and detect repeated failures despite byte changes. This repair does not demonstrate that the course model will autonomously complete the task. Formal independent review and user acceptance are still separate steps.
