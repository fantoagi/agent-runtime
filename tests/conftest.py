from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def workspace() -> Path:
    """A sandbox-friendly disposable test directory without pytest's tmp_path ACL handling."""
    path = Path.cwd() / ".runtime-test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
