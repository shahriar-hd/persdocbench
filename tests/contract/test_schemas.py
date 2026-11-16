"""Contract tests: every schema in /schemas must be valid JSON Schema 2020-12."""

from __future__ import annotations

import pathlib

import jsonschema
import pytest

REQUIRED_SCHEMAS = [
    "annotations.schema.json",
    "page-metadata.schema.json",
    "table.schema.json",
    "understanding.schema.json",
    "run-manifest.schema.json",
]


@pytest.mark.parametrize("name", REQUIRED_SCHEMAS)
def test_schema_exists_and_is_valid(schema_dir: pathlib.Path, name: str) -> None:
    path = schema_dir / name
    assert path.is_file(), f"missing schema: {name}"
    import json

    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("name", REQUIRED_SCHEMAS)
def test_schema_declares_id_and_version(schema_dir: pathlib.Path, name: str) -> None:
    import json

    schema = json.loads((schema_dir / name).read_text(encoding="utf-8"))
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["$id"].startswith("https://")
