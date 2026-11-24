# Browser button failure: diagnosis and repair candidate

The submitted `app.py` uses an ordinary Python triple-quoted `INDEX_HTML` string. Python consumes the backslash escapes intended for JavaScript's cancellation-button markup. The rendered script contains `cancelBooking(''+b.id+'')` inside a single-quoted JS string; parsing fails before `init()` runs, disabling all buttons and room initialization.

`app.py` in this directory is a repair candidate copied from the submitted source with one change: `INDEX_HTML = r"""...` preserves the JavaScript escapes. Original immutable artifacts and the live acceptance request were not modified.

Verified with Node.js v22.20.0 static compilation (no generated code executed):

- Original rendered JavaScript: exit 1, `SyntaxError: Unexpected string`.
- Candidate rendered JavaScript and three static inline handlers: all four syntax checks pass.
- Platform regression evidence: see `docs/member-a-work-report.md`; the exact ordinary-string versus raw-string failure is covered by `tests/test_ui_check.py`.

The headless browser test could not produce a browser report in the Codex execution environment (browser exit 2147483651). Therefore browser button behavior is not claimed as verified here. A normal terminal can run:

```powershell
& 'D:\Anaconda\python.exe' scripts/check_booking_buttons.py --app fixes/booking-ui/app.py
```

This uses a real headless browser with a mocked API to test room initialization, availability, booking feedback, duplicate conflict, cancellation, and rebooking. Actual backend HTTP tests and a manual browser run remain separately required.

Reject the current acceptance request with this defect, let the team repair and publish a new immutable app artifact, then review and preview that new submission. Do not approve the old submission based on this separate candidate.
