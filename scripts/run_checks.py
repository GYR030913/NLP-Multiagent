"""Run offline checks and save an auditable Markdown report (no API calls)."""
import io
import platform
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))


def main():
    import os
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=BASE / "runtime" / "verification.md")
    args = parser.parse_args()
    os.chdir(BASE)
    suite = unittest.defaultTestLoader.discover(str(BASE / 'tests'), top_level_dir=str(BASE))
    output = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    elapsed = time.monotonic() - started
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    lines = ['# Offline verification', '', f'- UTC: {datetime.now(timezone.utc).isoformat()}',
             f'- Python: {platform.python_version()}', f'- Executed tests: {result.testsRun}',
             f'- Passed: {passed}', f'- Skipped: {len(result.skipped)}',
             f'- Failures: {len(result.failures)}', f'- Errors: {len(result.errors)}',
             f'- Elapsed seconds: {elapsed:.3f}', '',
             'These tests use fake model responses and mocked Docker processes. They do not measure model task quality.',
             'Windows symlink checks may be skipped if link creation is unavailable.', '', '```text', output.getvalue(), '```']
    path = args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'Passed={passed}; skipped={len(result.skipped)}; failed={len(result.failures) + len(result.errors)}. Report: {path}')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
