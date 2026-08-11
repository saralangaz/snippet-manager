"""Shared pytest fixtures.

Every test gets its own throwaway data file — nothing here ever touches the
real data/snippets.yaml that a developer running the suite might have on
disk. app.SNIPPETS_FILE is a plain module global read by load_snippets/
save_snippets at call time (not captured at import time), so monkeypatching
it before each test correctly redirects every read/write for that test.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "snippets.yaml"
    monkeypatch.setattr(app_module, "SNIPPETS_FILE", str(path))
    return path


@pytest.fixture
def client(data_file):
    return TestClient(app_module.app)
