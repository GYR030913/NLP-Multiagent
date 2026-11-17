
"""Aggregate evaluation metrics across benchmark scenarios."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)

from collab.report import metrics


GROUPS = ("software", "newsroom", "fund")
TASKS_PER_GROUP = 2


def evaluate_benchmark(runtime):
    runtime = Path(runtime)

    scenarios = {}
    selected_rows = []

    for group in GROUPS:
        state_path = runtime / group / "state.json"

        scenario_result = {
            "group": group,
            "group_exists": state_path.exists(),
            "tasks": {},
            "excluded_runs": [],
            "duplicate_runs": [],
        }

        scenarios[group] = scenario_result

        # Initialize all expected benchmark tasks.
        for number in range(1, TASKS_PER_GROUP + 1):
            scenario_result["tasks"][f"task_{number}"] = {
                "task_number": number,
                "status": "not_started",
                "run_id": None,
                "success": False,
                "metrics": None,
            }

        # Missing groups remain not started.
        if not state_path.exists():
            continue

        state = json.loads(
            state_path.read_text(encoding="utf-8")
        )

        scenario_tasks = state["scenario"]["tasks"]

        if len(scenario_tasks) != TASKS_PER_GROUP:
            raise ValueError(
                f"{group}: expected "
                f"{TASKS_PER_GROUP} benchmark tasks"
            )

        # Map original task text to benchmark task number.
        task_lookup = {
            task["text"]: number
            for number, task in enumerate(
                scenario_tasks, start=1
            )
        }

        if len(task_lookup) != TASKS_PER_GROUP:
            raise ValueError(
                f"{group}: duplicate scenario task text"
            )

        # Reuse the existing per-group evaluator.
        group_metrics = metrics(state)

        rows_by_id = {
            row["run_id"]: row
            for row in group_metrics["runs"]
        }

        # First attempt is selected by start time.
        ordered_runs = sorted(
            state["runs"].items(),
            key=lambda item: item[1]["started_at"]
        )

        for run_id, run in ordered_runs:

            # Exclude private tasks.
            if run.get("private"):
                scenario_result["excluded_runs"].append({
                    "run_id": run_id,
                    "reason": "private",
                })
                continue

            root_task = state["tasks"][run["root"]]
            description = root_task["description"]

            number = task_lookup.get(description)

            # Exclude custom tasks.
            if number is None:
                scenario_result["excluded_runs"].append({
                    "run_id": run_id,
                    "reason": "not_benchmark_task",
                })
                continue

            task_result = scenario_result["tasks"][
                f"task_{number}"
            ]

            # Retain duplicate attempts without counting
            # the same benchmark task more than once.
            if task_result["run_id"] is not None:
                scenario_result["duplicate_runs"].append({
                    "run_id": run_id,
                    "task_number": number,
                    "selected_run_id": task_result["run_id"],
                    "metrics": rows_by_id[run_id],
                })
                continue

            row = rows_by_id[run_id]

            task_result.update({
                "run_id": run_id,
                "status": row["status"],
                "success": row["success"],
                "metrics": row,
            })

            selected_rows.append(row)

    # Aggregate benchmark metrics.
    expected_tasks = len(GROUPS) * TASKS_PER_GROUP

    started_tasks = len(selected_rows)

    completed_tasks = sum(
        row["success"]
        for row in selected_rows
    )

    not_started_tasks = expected_tasks - started_tasks

    total_subtasks = sum(
        row["subtasks_total"]
        for row in selected_rows
    )

    completed_subtasks = sum(
        row["subtasks_completed"]
        for row in selected_rows
    )

    total_duration = round(
        sum(
            row["duration_seconds"]
            for row in selected_rows
        ),
        3
    )

    total_human_wait = round(
        sum(
            row["human_wait_seconds"]
            for row in selected_rows
        ),
        3
    )

    total_model_calls = sum(
        row["model_calls"]
        for row in selected_rows
    )

    total_reported_tokens = sum(
        row["reported_tokens"]
        for row in selected_rows
    )

    return {
        "expected_tasks": expected_tasks,
        "started_tasks": started_tasks,
        "completed_tasks": completed_tasks,
        "not_started_tasks": not_started_tasks,

        "benchmark_completion_rate": (
            completed_tasks / expected_tasks
        ),

        "attempted_task_success_rate": (
            completed_tasks / started_tasks
            if started_tasks else None
        ),

        "total_subtasks": total_subtasks,
        "completed_subtasks": completed_subtasks,

        "subtask_completion_rate": (
            completed_subtasks / total_subtasks
            if total_subtasks else None
        ),

        "total_duration_seconds": total_duration,

        "average_duration_seconds": (
            round(total_duration / started_tasks, 3)
            if started_tasks else None
        ),

        "total_human_wait_seconds": total_human_wait,
        "total_model_calls": total_model_calls,
        "total_reported_tokens": total_reported_tokens,

        "scenarios": scenarios,

        "measurement": (
            "Six expected benchmark tasks. "
            "Tasks are matched by exact scenario text. "
            "Only the first attempt of each task is "
            "included in aggregate metrics. "
            "Private and custom runs are excluded. "
            "Duplicate attempts are recorded separately. "
            "Unfinished and unstarted tasks are not "
            "counted as successful. "
            "This is not an official instructor score."
        ),
    }


def format_percentage(value):
    """Format a rate for human-readable reports."""
    return f"{value:.2%}" if value is not None else "N/A"


def render_benchmark_report(result):
    """Convert benchmark metrics into a Markdown report."""

    average_duration = result["average_duration_seconds"]

    lines = [
        "# Multi-Agent Benchmark Evaluation Report",
        "",
        "Success requires independent review and human acceptance.",
        "This report is not an official instructor score.",
        "",
        "## 1. Overall Performance",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Expected tasks | {result['expected_tasks']} |",
        f"| Started tasks | {result['started_tasks']} |",
        f"| Completed tasks | {result['completed_tasks']} |",
        f"| Not started tasks | {result['not_started_tasks']} |",
        f"| Benchmark completion rate | {format_percentage(result['benchmark_completion_rate'])} |",
        f"| Attempted task success rate | {format_percentage(result['attempted_task_success_rate'])} |",
        f"| Total work subtasks | {result['total_subtasks']} |",
        f"| Completed work subtasks | {result['completed_subtasks']} |",
        f"| Global subtask completion rate | {format_percentage(result['subtask_completion_rate'])} |",
        f"| Sum of run durations (seconds) | {result['total_duration_seconds']} |",
        f"| Average run duration (seconds) | {average_duration if average_duration is not None else 'N/A'} |",
        f"| Total human wait (seconds) | {result['total_human_wait_seconds']} |",
        f"| Total model calls | {result['total_model_calls']} |",
        f"| Total reported tokens | {result['total_reported_tokens']} |",
        "",
        "## 2. Per-task Results",
        "",
        "| Scenario | Task | Run | Status | Success | Subtasks | Duration (s) | Human wait (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for group, scenario in result["scenarios"].items():
        for task in scenario["tasks"].values():

            row = task["metrics"]

            if row is None:
                subtasks = "N/A"
                duration = "N/A"
                human_wait = "N/A"
            else:
                subtasks = (
                    f"{row['subtasks_completed']}/"
                    f"{row['subtasks_total']}"
                )
                duration = row["duration_seconds"]
                human_wait = row["human_wait_seconds"]

            lines.append(
                f"| {group} | "
                f"{task['task_number']} | "
                f"{task['run_id'] or 'N/A'} | "
                f"{task['status']} | "
                f"{task['success']} | "
                f"{subtasks} | "
                f"{duration} | "
                f"{human_wait} |"
            )

    lines += [
        "",
        "## 3. Excluded and Duplicate Runs",
        "",
    ]

    for group, scenario in result["scenarios"].items():
        lines.append(f"### {group}")
        lines.append("")

        if not scenario["group_exists"]:
            lines.append("- Group state is missing.")

        excluded = scenario["excluded_runs"]
        duplicates = scenario["duplicate_runs"]

        if not excluded and not duplicates:
            lines.append("- No excluded or duplicate runs.")

        for run in excluded:
            lines.append(
                f"- Excluded {run['run_id']}: "
                f"{run['reason']}"
            )

        for run in duplicates:
            lines.append(
                f"- Duplicate {run['run_id']} "
                f"for Task {run['task_number']}; "
                f"selected first attempt: "
                f"{run['selected_run_id']}"
            )

        lines.append("")

    lines += [
        "## 4. Measurement Method",
        "",
        result["measurement"],
        "",
        "## 5. Limitations",
        "",
        "- Development trials and formal benchmark runs "
        "must be reported separately.",
        "- Elapsed time includes pauses and waiting.",
        "- Summed run durations are not batch wall-clock time.",
        "- Human waiting time may overlap productive work.",
        "- Duplicate attempts are excluded from primary totals.",
        "- Reported tokens depend on provider usage data.",
        "- Memory snapshots do not prove performance improvement.",
        "- This evaluator does not certify experimental conditions.",
        "",
    ]

    return "\n".join(lines)


def export_benchmark(runtime, output=None):
    """Generate JSON and Markdown benchmark reports."""

    runtime = Path(runtime)

    output = (
        Path(output)
        if output is not None
        else runtime / "benchmark"
    )

    result = evaluate_benchmark(runtime)

    output.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics_path = output / "benchmark_metrics.json"
    report_path = output / "benchmark_report.md"

    metrics_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    report_path.write_text(
        render_benchmark_report(result),
        encoding="utf-8",
    )

    return output


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate the six benchmark tasks."
    )

    parser.add_argument(
        "--runtime",
        default="runtime",
        help="Path to the benchmark runtime directory",
    )

    parser.add_argument(
        "--output",
        default=None,
        help="Optional custom report directory",
    )

    args = parser.parse_args()

    output = export_benchmark(
        runtime=args.runtime,
        output=args.output,
    )

    print("Benchmark evaluation completed.")
    print(f"Reports saved to: {output.resolve()}")


if __name__ == "__main__":
    main()
