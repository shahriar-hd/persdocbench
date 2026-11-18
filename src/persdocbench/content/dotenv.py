"""Minimal ``.env`` reader.

Deliberately not a dependency: the file format we need is ``KEY=value`` with
comments, optional quotes and no interpolation. ``python-dotenv`` would add a
package for twenty lines of parsing.

Precedence rule: **the real environment wins**. A value already present in
``os.environ`` is never overwritten, so a secret injected at launch (which is how
the sandbox passes credentials, see ADR-001) cannot be silently replaced by a
stale file on disk.

Nothing in this module prints, logs or returns an error message containing a
value: parsing errors name the key, never the secret.
"""
from __future__ import annotations

import os
from pathlib import Path


class EnvFileError(ValueError):
    """Raised for a malformed line, naming the line number only."""


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``.env`` content. Values keep internal spaces; quotes are stripped."""
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise EnvFileError(f"line {lineno}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise EnvFileError(f"line {lineno}: empty key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # strip an unquoted trailing comment: "value  # note"
            hash_at = value.find(" #")
            if hash_at != -1:
                value = value[:hash_at].rstrip()
        out[key] = value
    return out


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """Load a ``.env`` file into ``os.environ`` and return what was applied."""
    p = Path(path)
    if not p.is_file():
        return {}
    parsed = parse_dotenv(p.read_text(encoding="utf-8"))
    applied: dict[str, str] = {}
    for key, value in parsed.items():
        if not override and key in os.environ and os.environ[key]:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


def require(name: str) -> str:
    """Read a required environment variable, failing with the name, not the value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise KeyError(f"{name} is not set (checked os.environ and .env)")
    return value