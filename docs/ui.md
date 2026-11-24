# Campus Collab chat UI

The optional Gradio interface uses the existing Engine, Store, tool gateway and evaluation code. The CLI remains standard-library-only.

## Start

```bash
conda activate multiagent
cd /Users/rongchu/Projects/NLP-Multiagent
python -m pip install -r requirements-ui.txt
python scripts/run_ui.py --runtime runtime/ui_model_test --port 7861
```

Open http://127.0.0.1:7861. Existing tasks and `.env` are preserved. Restart the server after updating the code; use the same runtime to recover existing conversations. The server listens on loopback only, with sharing and analytics disabled.

## Chat

Choose a group and either Group chat or an Agent for a private conversation. Enter a message, select New task or Follow-up, and click **Send** (or submit the textbox). Send saves the message and immediately starts a background execution batch using the configured model. It can consume API credits. The default batch is 30 actions; adjust Actions per send before sending. Repeated identical task submissions do not create another task or start another paid batch.

The former benchmark task-launch bar has been removed. Evaluation and benchmark report export remain available in the bottom accordions. Original tasks can still be submitted via the CLI.

The responsive layout reserves the same chat width before and after the first message. The transcript uses 68% of the desktop viewport height (480–1000 px), with a vertical resize handle; narrow screens use 60% (360–720 px). Narrow screens stack navigation above chat without horizontal overflow. All application labels, status text and controls are English; user messages, original prompts, model responses and existing logs retain their original language.

## Pause, Continue and End

- **Pause** immediately sets the stop flag and shows “Pause requested”. No further model actions are scheduled. If a model request is in flight, its returned action is discarded before being applied, but reported usage and the attempted action budget are retained. A tool that has already started must finish or time out; Pause cannot undo external effects or guarantee cancellation of provider billing. The group writer lock is retained until the in-flight operation returns. The UI reports Paused only after that point.
- **Continue** resumes a paused/recovered task or starts another bounded batch after its step limit. Sending a new message already starts execution; Continue is not required for the first send.
- **End** ends active tasks in the selected conversation (public group or the selected Agent's private conversation). During execution, it first requests a stop, then records a human-abort event and uses Engine.end_run to mark the selected run failed, cancel pending requests and remove queued tasks. When idle or waiting for human input, it ends the selected run immediately under the same writer lock. It preserves history, artifacts and metrics, and never reports an aborted attempt as success. End is not Undo or successful acceptance.

Pause applies to the current group's UI worker, which uses the existing group scheduler. End only marks the selected conversation's runs failed. Other conversations remain persisted and can be continued. A CLI/other-process writer cannot be controlled by this UI instance; its file lock is respected.

## Human input and reporting

Pending clarification, tool approval and acceptance requests appear below the transcript. The oldest pending request is displayed automatically. Inspect the question/action/artifact IDs and explicitly answer, approve or reject. Successful responses clear the input and automatically resume runnable work; remaining requests appear in order. Rejection requires a reason. Approved tools may execute immediately. No extra Continue click is required after responding. No sensitive operation or final delivery is automatically approved.

New events automatically scroll the transcript to the latest message; unchanged refreshes preserve your reading position. Follow-up messages after completion create a new custom run with the same group history, leaving the accepted run unchanged. The dashboard shows actual metrics, not demonstration scores. Full metrics, benchmark evaluation, reports, delivery files and raw events are in expandable sections. Complete exports include private messages and must only be shared with authorized evaluators. Published artifact hashes are checked by the existing report backend. Downloads use a dedicated cache, without exposing the runtime or workspaces as served directories. Export trees and artifact blobs containing symlinks are rejected. Configured secrets are redacted in views; Web download is refused if an exported file contains a configured secret, preserving artifact bytes rather than silently rewriting hashes.

## Architecture and tests

```text
Gradio single-page UI → UIService → Engine / Store / Tools / report
                         ↓
                  evaluate_benchmark
```

UIService serializes writes with the existing CLI file lock. Each mutation reloads current state. Read-only refreshes use atomic persisted snapshots. The Engine's optional stop_requested callback is backward-compatible with CLI callers and is checked before scheduling and after a model response, before applying its action. The UI does not replace the HITL state machine or Docker isolation.

```bash
python scripts/run_checks.py --output docs/ui-verification.md
python -m unittest tests.test_ui_service tests.test_ui_views -v
```

The custom output path preserves personal modifications to docs/verification.md. Tests use Fake Model/Mock and cover auto-start, duplicate submission, pause/discard/resume, end during execution, private conversation end isolation, approval, locks, exports and metrics. Optional Gradio tests check the actual component/callback wiring. Browser smoke tests use a separate temporary runtime and Fake Model. No real model quality or formal six-task experiment results are implied.

Known limits: full event history is re-rendered without pagination; an in-flight request cannot be forcibly cancelled; abrupt process termination retains the backend's existing lack of exactly-once external side-effect guarantees. This is a local single-operator UI, not a multi-tenant authorization system.

## Downloads

The download list shows selected deliverables with `delivery__<run>__<path>` names, evaluation reports, and dialogue files. `performance_report.md` is the platform performance report, not the generated news article. `complete_execution_archive.zip` contains the full directory structure, including every published artifact version and audit data. Older drafts are intentionally preserved inside the archive instead of appearing as ambiguous duplicate filenames in the main list. Files marked as delivery are selected by the root task; inspect the delivery manifest to distinguish accepted delivery from a draft.
