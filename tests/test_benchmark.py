
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_benchmark import (
    evaluate_benchmark,
    export_benchmark,
)


GROUPS = ("software", "newsroom", "fund")


class BenchmarkTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp_dir.name)

        self.states = {
            group: self.make_state(group)
            for group in GROUPS
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_state(self, group):
        return {
            "scenario": {
                "tasks": [
                    {"text": f"{group} benchmark task 1"},
                    {"text": f"{group} benchmark task 2"},
                ]
            },
            "runs": {},
            "tasks": {},
            "requests": {},
        }

    def add_run(
        self,
        group,
        number,
        status="completed",
        subtask_statuses=(),
        private=False,
        custom_text=None,
    ):
        state = self.states[group]

        run_id = f"r{len(state['runs']) + 1}"
        root_id = f"{run_id}_root"

        description = (
            custom_text
            if custom_text is not None
            else state["scenario"]["tasks"][number - 1]["text"]
        )

        state["runs"][run_id] = {
            "id": run_id,
            "root": root_id,
            "status": status,
            "private": private,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": (
                None if status == "running"
                else "2026-01-01T00:00:10+00:00"
            ),
            "fatal_error": False,
            "model_calls": 2,
            "tokens": 100,
            "provider": "test",
            "model": "fake-model",
            "memory_before": {},
            "memory_after": {},
        }

        state["tasks"][root_id] = {
            "id": root_id,
            "run": run_id,
            "kind": "work",
            "parent": None,
            "description": description,
            "status": status,
        }

        for index, child_status in enumerate(subtask_statuses):
            child_id = f"{run_id}_child_{index}"

            state["tasks"][child_id] = {
                "id": child_id,
                "run": run_id,
                "kind": "work",
                "parent": root_id,
                "status": child_status,
            }

    def save_states(self):
        for group, state in self.states.items():
            path = self.runtime / group / "state.json"

            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            path.write_text(
                json.dumps(state),
                encoding="utf-8",
            )

    def evaluate(self):
        self.save_states()
        return evaluate_benchmark(self.runtime)

    def test_empty_benchmark(self):
        result = self.evaluate()

        self.assertEqual(result["expected_tasks"], 6)
        self.assertEqual(result["started_tasks"], 0)
        self.assertEqual(result["completed_tasks"], 0)
        self.assertEqual(result["not_started_tasks"], 6)

        self.assertEqual(
            result["benchmark_completion_rate"], 0.0
        )

        self.assertIsNone(
            result["attempted_task_success_rate"]
        )

        self.assertIsNone(
            result["subtask_completion_rate"]
        )

    def test_partial_benchmark(self):
        self.add_run(
            "software",
            1,
            subtask_statuses=["completed"] * 4,
        )

        result = self.evaluate()

        self.assertEqual(result["started_tasks"], 1)
        self.assertEqual(result["completed_tasks"], 1)
        self.assertEqual(result["not_started_tasks"], 5)

        self.assertAlmostEqual(
            result["benchmark_completion_rate"], 1 / 6
        )

        self.assertEqual(
            result["attempted_task_success_rate"], 1.0
        )

        self.assertEqual(result["total_subtasks"], 4)
        self.assertEqual(result["completed_subtasks"], 4)

        self.assertEqual(
            result["total_duration_seconds"], 10.0
        )

        self.assertEqual(result["total_model_calls"], 2)
        self.assertEqual(result["total_reported_tokens"], 100)

    def test_full_benchmark(self):
        for group in GROUPS:
            for number in (1, 2):
                self.add_run(group, number)

        result = self.evaluate()

        self.assertEqual(result["started_tasks"], 6)
        self.assertEqual(result["completed_tasks"], 6)
        self.assertEqual(result["not_started_tasks"], 0)

        self.assertEqual(
            result["benchmark_completion_rate"], 1.0
        )

        self.assertEqual(
            result["attempted_task_success_rate"], 1.0
        )

        self.assertEqual(
            result["total_duration_seconds"], 60.0
        )

        self.assertEqual(
            result["average_duration_seconds"], 10.0
        )

    def test_mixed_statuses_and_weighted_subtasks(self):
        self.add_run(
            "software",
            1,
            status="completed",
            subtask_statuses=["completed"] * 4,
        )

        self.add_run(
            "newsroom",
            1,
            status="failed",
            subtask_statuses=["completed", "failed"],
        )

        self.add_run(
            "fund",
            1,
            status="running",
            subtask_statuses=[
                "completed",
                "running",
                "running",
                "running",
            ],
        )

        result = self.evaluate()

        self.assertEqual(result["started_tasks"], 3)
        self.assertEqual(result["completed_tasks"], 1)
        self.assertEqual(result["not_started_tasks"], 3)

        self.assertAlmostEqual(
            result["attempted_task_success_rate"], 1 / 3
        )

        self.assertEqual(result["total_subtasks"], 10)
        self.assertEqual(result["completed_subtasks"], 6)

        self.assertAlmostEqual(
            result["subtask_completion_rate"], 0.6
        )

    def test_private_and_custom_runs_excluded(self):
        self.add_run("software", 1)

        self.add_run(
            "software",
            2,
            private=True,
        )

        self.add_run(
            "newsroom",
            1,
            custom_text="This is not a benchmark task.",
        )

        result = self.evaluate()

        self.assertEqual(result["started_tasks"], 1)
        self.assertEqual(result["completed_tasks"], 1)
        self.assertEqual(result["total_model_calls"], 2)
        self.assertEqual(result["total_reported_tokens"], 100)

    def test_duplicate_runs_do_not_inflate_task_count(self):
        self.add_run("software", 1)
        self.add_run("software", 1)

        result = self.evaluate()

        self.assertEqual(result["started_tasks"], 1)
        self.assertEqual(result["completed_tasks"], 1)

        self.assertAlmostEqual(
            result["benchmark_completion_rate"], 1 / 6
        )

    def test_export_generates_json_and_markdown(self):
        self.add_run(
            "software",
            1,
            subtask_statuses=["completed"] * 4,
        )

        self.save_states()

        state_path = (
                self.runtime / "software" / "state.json"
        )

        original_state = state_path.read_bytes()

        output = export_benchmark(self.runtime)

        metrics_path = output / "benchmark_metrics.json"
        report_path = output / "benchmark_report.md"

        self.assertTrue(metrics_path.is_file())
        self.assertTrue(report_path.is_file())

        data = json.loads(
            metrics_path.read_text(encoding="utf-8")
        )

        report = report_path.read_text(
            encoding="utf-8"
        )

        self.assertEqual(data["expected_tasks"], 6)
        self.assertEqual(data["completed_tasks"], 1)

        self.assertIn(
            "# Multi-Agent Benchmark Evaluation Report",
            report,
        )

        self.assertIn(
            "| Benchmark completion rate | 16.67% |",
            report,
        )

        self.assertIn(
            "| Attempted task success rate | 100.00% |",
            report,
        )

        self.assertIn(
            "| newsroom | 2 | N/A | not_started |",
            report,
        )

        # Export must not modify the original state.
        self.assertEqual(
            state_path.read_bytes(),
            original_state,
        )

    def test_missing_group_is_not_started(self):
        self.save_states()

        missing_path = (
                self.runtime / "fund" / "state.json"
        )

        missing_path.unlink()

        result = evaluate_benchmark(self.runtime)

        self.assertEqual(result["expected_tasks"], 6)
        self.assertEqual(result["not_started_tasks"], 6)

        fund = result["scenarios"]["fund"]

        self.assertFalse(fund["group_exists"])

        self.assertEqual(
            fund["tasks"]["task_1"]["status"],
            "not_started",
        )

        self.assertEqual(
            fund["tasks"]["task_2"]["status"],
            "not_started",
        )

    def test_first_failed_attempt_is_not_replaced(self):
        self.add_run(
            "software",
            1,
            status="failed",
        )

        self.add_run(
            "software",
            1,
            status="completed",
        )

        second_run = self.states["software"]["runs"]["r2"]

        second_run["started_at"] = (
            "2026-01-01T00:01:00+00:00"
        )

        second_run["finished_at"] = (
            "2026-01-01T00:01:10+00:00"
        )

        result = self.evaluate()

        self.assertEqual(result["started_tasks"], 1)
        self.assertEqual(result["completed_tasks"], 0)
        self.assertEqual(result["benchmark_completion_rate"], 0.0)

        software = result["scenarios"]["software"]

        self.assertEqual(
            software["tasks"]["task_1"]["run_id"],
            "r1",
        )

        self.assertEqual(
            software["tasks"]["task_1"]["status"],
            "failed",
        )

        self.assertEqual(
            len(software["duplicate_runs"]),
            1,
        )

        self.assertEqual(
            software["duplicate_runs"][0]["run_id"],
            "r2",
        )

        self.assertEqual(result["total_model_calls"], 2)
        self.assertEqual(result["total_reported_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
