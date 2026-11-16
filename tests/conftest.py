"""Shared pytest fixtures."""

from __future__ import annotations

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def schema_dir() -> pathlib.Path:
    """Directory holding the JSON Schemas that form the data contract."""
    return SCHEMA_DIR


@pytest.fixture(scope="session")
def load_schema():
    """Return a function that loads one schema by file name."""

    def _load(name: str) -> dict:
        return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))

    return _load
