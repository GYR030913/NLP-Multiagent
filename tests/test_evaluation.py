
import unittest
from unittest.mock import patch

from collab.report import metrics


def make_run(
    run_id,
    status,
    started_at,
    finished_at,
    private=False
):
    return {
        'id': run_id,
        'status': status,
        'started_at': started_at,
        'finished_at': finished_at,
        'private': private,
        'fatal_error': False,
        'model_calls': 0,
        'tokens': 0,
        'provider': 'hkgai',
        'model': 'test-model',
        'memory_before': {},
    }


class GlobalMetricsTests(unittest.TestCase):

    def test_global_metrics_aggregate_multiple_runs(self):

        state = {
            'runs': {
                'r1': make_run(
                    'r1', 'completed',
                    '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:10+00:00'
                ),
                'r2': make_run(
                    'r2', 'failed',
                    '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:20+00:00'
                ),
                'r3': make_run(
                    'r3', 'running',
                    '2026-01-01T00:00:00+00:00',
                    None
                ),
                'private': make_run(
                    'private', 'completed',
                    '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:01:40+00:00',
                    private=True
                ),
            },

            'tasks': {
                # Run 1: 1/1 subtasks completed
                't1': {
                    'run': 'r1', 'kind': 'work',
                    'parent': None, 'status': 'completed'
                },
                't2': {
                    'run': 'r1', 'kind': 'work',
                    'parent': 't1', 'status': 'completed'
                },

                # Run 2: 1/3 subtasks completed
                't3': {
                    'run': 'r2', 'kind': 'work',
                    'parent': None, 'status': 'failed'
                },
                't4': {
                    'run': 'r2', 'kind': 'work',
                    'parent': 't3', 'status': 'completed'
                },
                't5': {
                    'run': 'r2', 'kind': 'work',
                    'parent': 't3', 'status': 'failed'
                },
                't6': {
                    'run': 'r2', 'kind': 'work',
                    'parent': 't3', 'status': 'failed'
                },

                # Review task must not count as a work subtask
                't7': {
                    'run': 'r2', 'kind': 'review',
                    'parent': 't3', 'status': 'completed'
                },

                # Run 3: unfinished
                't8': {
                    'run': 'r3', 'kind': 'work',
                    'parent': None, 'status': 'running'
                },

                # Private run must not be counted
                't9': {
                    'run': 'private', 'kind': 'work',
                    'parent': None, 'status': 'completed'
                },
                't10': {
                    'run': 'private', 'kind': 'work',
                    'parent': 't9', 'status': 'completed'
                },
            },

            'requests': {
                # Run 1: 3 seconds waiting
                'q1': {
                    'task': 't1',
                    'created_at': '2026-01-01T00:00:02+00:00',
                    'answered_at': '2026-01-01T00:00:05+00:00'
                },

                # Run 2: overlapping intervals = 10 seconds
                'q2': {
                    'task': 't3',
                    'created_at': '2026-01-01T00:00:05+00:00',
                    'answered_at': '2026-01-01T00:00:12+00:00'
                },
                'q3': {
                    'task': 't4',
                    'created_at': '2026-01-01T00:00:08+00:00',
                    'answered_at': '2026-01-01T00:00:15+00:00'
                },
            }
        }

        with patch(
            'collab.report.now',
            return_value='2026-01-01T00:00:30+00:00'
        ):
            result = metrics(state)

        self.assertEqual(result['total_runs'], 3)
        self.assertEqual(result['completed_runs'], 1)

        self.assertAlmostEqual(
            result['task_completion_rate'], 1 / 3
        )

        self.assertEqual(result['total_subtasks'], 4)
        self.assertEqual(result['completed_subtasks'], 2)

        # Weighted completion rate: 2 / 4 = 0.5
        self.assertAlmostEqual(
            result['subtask_completion_rate'], 0.5
        )

        self.assertEqual(result['total_duration_seconds'], 60.0)
        self.assertEqual(result['average_duration_seconds'], 20.0)

        # 3 seconds + 10 seconds (overlapping intervals merged)
        self.assertEqual(result['total_human_wait_seconds'], 13.0)

    def test_empty_evaluation_returns_safe_values(self):

        state = {
            'runs': {},
            'tasks': {},
            'requests': {},
        }

        result = metrics(state)

        self.assertEqual(result['total_runs'], 0)
        self.assertEqual(result['completed_runs'], 0)
        self.assertIsNone(result['task_completion_rate'])

        self.assertEqual(result['total_subtasks'], 0)
        self.assertEqual(result['completed_subtasks'], 0)
        self.assertIsNone(result['subtask_completion_rate'])

        self.assertEqual(result['total_duration_seconds'], 0.0)
        self.assertIsNone(result['average_duration_seconds'])
        self.assertEqual(result['total_human_wait_seconds'], 0.0)


class ExportConsistencyTests(unittest.TestCase):

    def test_export_calculates_metrics_only_once(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import patch
        from collab import report

        state = {
            "runs": {},
            "tasks": {},
            "requests": {},
            "sources": {},
            "events": [],
            "artifacts": {},
        }

        with TemporaryDirectory() as directory:
            store = SimpleNamespace(path=Path(directory))

            with patch(
                "collab.report.metrics",
                wraps=report.metrics,
            ) as calculate:

                report.export(store, state)

                calculate.assert_called_once_with(state)


if __name__ == '__main__':
    unittest.main()
