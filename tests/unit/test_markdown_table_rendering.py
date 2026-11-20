"""Markdown table separators must never render as data rows.

Observed defect (2026-11-19): a real invoice carried the separator row
``|---|------------|----------|--------------|``. The renderer and the markdown
preview both treated a row as a separator only when every cell was literally
``-``/``---``/``:---:``, so the long-dash separator leaked into the rendered
table as a visible ``---`` row.

The predicate now lives in :mod:`persdocbench.content.markdown` and both the PDF
renderer and the preview import it. These tests import the *package module*, never
the renderer script — ``scripts/render_samples.py`` needs weasyprint and pymupdf,
which the CI dev environment does not install.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from persdocbench.content.markdown import (
    is_separator_line,
    is_separator_row,
    is_table_row,
    separator_for,
    split_row,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> object:
    """Import a script by module name without importing the renderer's PDF deps."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module(name)


@pytest.mark.parametrize(
    "cells",
    [
        ["---", "---", "---"],
        ["-", "-"],
        [":---", "---:", ":---:"],
        ["---", "------------", "----------", "--------------"],
    ],
)
def test_separator_rows_are_recognised(cells: list[str]) -> None:
    assert is_separator_row(cells)


@pytest.mark.parametrize(
    "cells",
    [
        ["1", "کالا", "۱۵۰,۰۰۰"],
        ["ردیف", "شرح", "مبلغ"],
        ["---", "متن"],  # a real row where only one cell is dashes
        [],
    ],
)
def test_data_rows_are_not_mistaken_for_separators(cells: list[str]) -> None:
    assert not is_separator_row(cells)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("| --- | --- |", True),
        ("|---|------------|----------|--------------|", True),
        ("| :--- | ---: |", True),
        ("| ۱ | کالا | ۱۵۰,۰۰۰ |", False),
        ("| --- | متن |", False),
        ("خط بدون پایپ", False),
    ],
)
def test_separator_line_helper(line: str, expected: bool) -> None:
    assert is_separator_line(line) is expected


def test_split_row_strips_cells_and_outer_pipes() -> None:
    assert split_row("| ردیف |  شرح |") == ["ردیف", "شرح"]
    assert split_row("|1|2|") == ["1", "2"]
    assert split_row("|-|-|") == ["-", "-"]


def test_is_table_row_requires_leading_and_trailing_pipe() -> None:
    assert is_table_row("| a | b |")
    assert is_table_row("  | a |  ")
    assert not is_table_row("|-")
    assert not is_table_row("a | b |")


def test_separator_for_matches_header_width() -> None:
    sep = separator_for(["a", "b", "c"])
    assert split_row(sep) == ["---", "---", "---"]
    assert is_separator_line(sep)


def test_preview_and_package_agree_on_the_predicate() -> None:
    """The preview must use the shared predicate, not a private copy."""
    prev = _load("preview_markdown")
    sep = ["---", "------------", "----------"]
    assert prev.is_separator_row is is_separator_row
    assert prev.is_separator_row(sep) == is_separator_row(sep)


def test_preview_inserts_one_separator_per_table() -> None:
    prev = _load("preview_markdown")
    text = "| ردیف | شرح |\n| --- | --- |\n| ۱ | کالا |\n\nپاراگراف بعدی"
    out = prev.add_table_separators(text)  # type: ignore[attr-defined]
    assert out.count("| --- |") == 1
    assert "پاراگراف بعدی" in out


def test_preview_keeps_header_above_an_existing_separator() -> None:
    """A long-dash separator already in the text must not be duplicated."""
    prev = _load("preview_markdown")
    text = "| الف | ب |\n|---|------------|\n| ۱ | ۲ |"
    out = prev.add_table_separators(text)  # type: ignore[attr-defined]
    rows = [ln for ln in out.splitlines() if is_separator_line(ln)]
    assert len(rows) == 1, f"expected one separator, got {rows}"
    header_pos = out.index("| الف | ب |")
    sep_pos = out.index(rows[0])
    assert header_pos < sep_pos, "separator must follow the header, never precede it"
