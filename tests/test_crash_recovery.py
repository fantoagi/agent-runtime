from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.crash
def test_forced_process_crash_recovery_matrix(workspace: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_crash_recovery.py",
            "--workspace",
            str(workspace / "crash-matrix"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"status": "passed"' in completed.stdout
