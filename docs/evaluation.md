# Multi-Agent Benchmark Evaluation Protocol

## 1. Objective

This protocol defines a reproducible evaluation procedure for
the multi-agent collaboration platform.

The evaluation covers:

- Overall goal completion
- Delegated subtask completion
- Execution time
- Human-in-the-loop interaction
- Model calls and reported token usage
- Error recovery
- Independent review and revision
- Persistent memory evolution

The evaluation results are platform-level measurements,
not official instructor scores.

## 2. Benchmark Scope

The benchmark contains three independent scenarios
and six predefined tasks.

| Group    | Task 1                        | Task 2                              |
| -------- | ----------------------------- | ----------------------------------- |
| Software | Meeting-room booking system   | Room usage dashboard                |
| Newsroom | Campus AI news report         | AI-writing detector fact-checking   |
| Fund     | Investment portfolio proposal | Semiconductor industry presentation |

The original task descriptions are stored in NLP_Mid_Test/.

Each scenario must execute Task 1 before Task 2.

Task 2 must reuse the same scenario group so that the
agents can access previous artifacts, dialogue and memory.

Different scenarios must remain isolated.

## 3. Experimental Controls

Before a formal benchmark, record:

- Git commit hash
- Git working tree status
- Experiment identifier
- Experiment date
- LLM provider
- Model identifier
- Relevant model configuration
- Python version
- Operating system
- Docker sandbox image version or identifier
- Task execution order
- Initial agent memory condition
- Search availability and data cutoff date

Do not record API keys, access tokens or other credentials.

All six tasks within a formal benchmark should use
the same code version and model configuration.

If the code or model changes, record the change and
treat the affected experiment as a separate trial.

## 4. Runtime Isolation

Development trials and formal benchmark experiments
must use separate runtime directories.

Development runtime:

    runtime/
        software/
        newsroom/
        fund/
        benchmark/

Example formal experiment:

    runtime/formal_001/
        software/
        newsroom/
        fund/
        benchmark/

A new formal experiment should begin with fresh groups
and empty agent memory.

Existing development states must not be deleted or
overwritten to improve benchmark results.

Within each scenario, memory must be preserved
between Task 1 and Task 2.

## 5. Execution Procedure

For each scenario:

1. Initialize a fresh scenario group.
2. Submit the original benchmark Task 1.
3. Allow autonomous planning and task delegation.
4. Respond to genuine clarification requests.
5. Observe agent collaboration and tool execution.
6. Allow independent review and necessary revisions.
7. Perform human acceptance checks.
8. Record the final task status.
9. Export the execution trace and performance metrics.
10. Submit Task 2 in the same group.
11. Repeat the evaluation and export procedure.

Do not manually change task status or fabricate
successful execution evidence.

The detailed scenario acceptance suggestions are
defined in docs/benchmark.md.

## 6. Human-in-the-Loop Protocol

Human intervention is permitted for:

- Requirements clarification
- Scope confirmation
- Approval of sensitive tool operations
- External environment recovery
- Manual verification
- Final acceptance or rejection

Every intervention should be recorded through
the platform's human request mechanism.

The human should not directly complete work that
is assigned to an autonomous agent.

If substantial manual assistance is required,
record its purpose and impact in the experiment notes.

Final acceptance must reflect actual observed evidence.

A task must not be approved merely to improve
the reported completion rate.

## 7. Success Criteria

A benchmark task is considered successful when:

- The requested deliverables are produced.
- Acceptance requirements are satisfied.
- Independent review is completed.
- The human accepts the final delivery.
- The run has no fatal error.

Task completion is different from objective output quality.

A completed run does not guarantee that every factual
claim or implementation detail is correct.

Human acceptance must consider scenario-specific
requirements and documented limitations.

## 8. Evaluation Metrics

### 8.1 Benchmark Completion Rate

Successful benchmark tasks / 6 expected benchmark tasks

Unstarted, unfinished and failed tasks are not counted
as successful.

### 8.2 Attempted Task Success Rate

Successful benchmark tasks / started benchmark tasks

If no benchmark task has started, the value is undefined
and reported as N/A.

### 8.3 Subtask Completion Rate

Completed delegated work subtasks / total delegated work subtasks

Root tasks and independent review nodes are excluded.

Across scenarios, subtasks are aggregated by count,
rather than averaging individual scenario percentages.

### 8.4 Execution Duration

The elapsed time from run creation to completion or
the current evaluation time for unfinished runs.

Total duration is the sum of selected run durations.

It is not necessarily the wall-clock duration of
the entire benchmark batch.

### 8.5 Human Waiting Time

Human waiting time is calculated using the union
of human-request waiting intervals within each run.

Overlapping intervals within one run are counted once.

Human waiting time may overlap productive work
performed by other agents.

### 8.6 Model Calls and Token Usage

Model calls count the platform's recorded model-call
attempts.

Reported tokens are based on usage information
available from the model provider.

These values may not include every internal retry
or failed provider request.

### 8.7 Memory Evolution

Compare memory snapshots before and after each run.

Inspect persistent memory records for:

- Lessons learned from task execution
- Lessons learned from review feedback
- Corrections following rejected work
- Reuse of relevant experience during Task 2

Changed memory hashes indicate memory changes,
not necessarily improved agent performance.

## 9. Failure and Retry Policy

All failed attempts, interrupted runs and recovery
events must be preserved.

Temporary API or infrastructure failures should be
resolved without recreating an existing task.

When possible, resume the same run.

The benchmark evaluator uses the first matching
public run of each original benchmark task.

Duplicate attempts are recorded separately and
excluded from the primary aggregate metrics.

Private tasks and unrelated custom tasks are excluded.

Task matching uses the exact original scenario text.

A new attempt must not silently replace a failed
first attempt in the primary benchmark report.

## 10. Evidence Collection

Preserve the following evidence where available:

- state.json
- dialogue.jsonl
- dialogue.md
- metrics.json
- performance_report.md
- sources.json
- Published artifacts
- Delivery manifests
- Independent review results
- Human clarification and acceptance records
- Agent memory records
- Manual verification notes and screenshots

Preserve both successful and unsuccessful evidence.

Private dialogue and sensitive runtime data must
only be shared with authorized project members
or evaluators.

## 11. Report Generation

Development evaluation:

    python scripts/evaluate_benchmark.py

Formal evaluation example:

    python scripts/evaluate_benchmark.py --runtime runtime/formal_001

The evaluator generates:

    benchmark_metrics.json
    benchmark_report.md

The JSON report is intended for programmatic analysis.

The Markdown report is intended for human review.

Both reports are generated from the same evaluation
result to preserve consistency.

## 12. Experiment Record Template

| Field                              | Value                |
| ---------------------------------- | -------------------- |
| Experiment ID                      |                      |
| Experiment type                    | Development / Formal |
| Date                               |                      |
| Git commit                         |                      |
| Working tree clean                 |                      |
| Provider                           |                      |
| Model                              |                      |
| Python version                     |                      |
| Operating system                   |                      |
| Docker image                       |                      |
| Search configuration               |                      |
| Runtime directory                  |                      |
| Initial memory condition           |                      |
| Task order                         |                      |
| Human assistance notes             |                      |
| Network or infrastructure failures |                      |
| Known limitations                  |                      |

Complete this record using actual experimental data.

## 13. Limitations

The evaluator does not automatically verify that all experimental conditions are identical.

Human review quality may differ between experiments.

Reported token usage may be incomplete when provider requests fail.

Elapsed duration includes pauses and interruptions.

Memory changes alone do not demonstrate learning effectiveness.

Offline unit tests validate platform behavior, not the quality of real benchmark deliverables.

Development trial results and formal benchmark results must be reported separately.