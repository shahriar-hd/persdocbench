"""Markdown pipe-table helpers shared by the renderer and the preview writer.

Observed defect (2026-11-19): a real invoice carried the separator row
``|---|------------|----------|--------------|``. Both the PDF renderer and the
markdown preview treated a row as a separator only when every cell was literally
``-``/``---``/``:---:``, so the long-dash separator leaked into the rendered table
as a visible ``---`` data row. One predicate now lives here and both sides import
it, so the two can never drift apart again.
"""

from __future__ import annotations

import re

#: A separator cell is dashes with optional alignment colons, e.g. ``---``,
#: ``:---``, ``---:``, ``:---:``, ``---------``. At least one dash is required,
#: which also accepts the short single-dash form some generators emit.
SEPARATOR_RE = re.compile(r"^:?-+:?$")


def split_row(row: str) -> list[str]:
    """Split a markdown pipe row into stripped cell values."""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def is_table_row(line: str) -> bool:
    """True when the stripped line looks like a markdown pipe row."""
    st = line.strip()
    return len(st) >= 2 and st.startswith("|") and st.endswith("|")


def is_separator_row(cells: list[str]) -> bool:
    """True when every cell is a dash/alignment cell — i.e. a separator row.

    An empty cell list is not a separator. A row where only *some* cells are
    dashes (e.g. ``| --- | متن |``) is real data, not a separator.
    """
    return bool(cells) and all(SEPARATOR_RE.match(c) for c in cells)


def is_separator_line(line: str) -> bool:
    """Convenience wrapper: separator test straight from a raw markdown line."""
    return is_table_row(line) and is_separator_row(split_row(line))


def separator_for(header_cells: list[str]) -> str:
    """Build a separator row matching the width of ``header_cells``."""
    return "| " + " | ".join("---" for _ in header_cells) + " |"
