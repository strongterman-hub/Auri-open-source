from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir() -> Path:
    """A project-local temp directory that does not rely on pytest's tmp_path."""
    base = Path(__file__).resolve().parent / ".tmp-tests"
    base.mkdir(parents=True, exist_ok=True)
    directory = base / uuid.uuid4().hex
    directory.mkdir()
    yield directory
    shutil.rmtree(directory, ignore_errors=True)
