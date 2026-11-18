#!/usr/bin/env python3
"""Enumerate locally installed fonts with Persian coverage and classify them.

Read-only. Never installs, downloads or modifies a font. Writes a JSON report to
``artifacts/fonts/local_fonts.json`` (git-ignored) and prints a short table.

Why this exists (D30): the user has "some regular fonts" on the machine and asked for
~30 Persian families in the benchmark. A machine can hold 700 font files and still
provide fewer than ten Persian-capable families that are *freely licensed*, so the local
inventory is measured rather than assumed, and every family is matched against the
verified inventory in ``configs/content/fonts_v1.yaml``.

Usage:
    python scripts/scan_local_fonts.py
    python scripts/scan_local_fonts.py --config configs/content/fonts_v1.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # optional; the scan degrades gracefully without PyYAML
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "configs" / "content" / "fonts_v1.yaml"
DEFAULT_OUT = REPO / "artifacts" / "fonts" / "local_fonts.json"

# Foundries and family names that are known to ship under a free licence. This is a
# *hint* used to sort the report, never a licence verdict: the verdict lives in
# fonts_v1.yaml with a licence-file sha behind it.
FREE_FOUNDRY_HINTS = ("GOOG", "ALIF", "PfEd", "UKWN")
FREE_FAMILY_HINTS = (
    "vazir", "sahel", "samim", "shabnam", "tanha", "gandom", "nahid", "parastoo",
    "estedad", "fandogh", "noqte", "daftar", "pfont", "behdad", "nika", "iranian",
    "ganjnameh", "farbod", "shahab", "noon", "erfan", "amiri", "noto", "scheherazade",
    "lateef", "harmattan", "lalezar", "markazi", "mirza", "rakkas", "gulzar", "vibes",
    "dejavu", "dastkhat", "mikhak", "ronevis", "evilfont",
)
PROPRIETARY_HINTS = ("IRANSans", "IRANYekan", "B Nazanin", "B Zar", "Yekan Bakh", "Dana")


def parse_fc_list_output(text: str) -> list[str]:
    """Split `fc-list :lang=fa family` output into unique family names."""
    families: set[str] = set()
    for line in text.splitlines():
        for part in line.split(","):
            name = part.strip()
            if name:
                families.add(name)
    return sorted(families)


def base_family(name: str) -> str:
    """`Sahel Black` and `Sahel` are one family; weights are not separate families."""
    for suffix in (" Black", " ExtraBold", " Bold", " SemiBold", " Medium", " Regular",
                   " Light", " ExtraLight", " Thin", "FaNum"):
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def classify(family: str, inventory: Iterable[str]) -> str:
    """Return one of verified_inventory / proprietary_hint / free_hint / unknown."""
    inv = {n.casefold() for n in inventory}
    fam = base_family(family)
    if fam.casefold() in inv:
        return "verified_inventory"
    for hint in PROPRIETARY_HINTS:
        if hint.casefold() in fam.casefold():
            return "proprietary_hint"
    for hint in FREE_FAMILY_HINTS:
        if hint.casefold() in fam.casefold():
            return "free_hint"
    return "unknown"


def run(cmd: Sequence[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


def scan(limit_families: int | None = None) -> tuple[list[str], int]:
    families = parse_fc_list_output(run(["fc-list", ":lang=fa", "family"]))
    total = len(run(["fc-list"]).splitlines())
    if limit_families:
        families = families[:limit_families]
    return families, total


def inventory_names(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (verified inventory names, names awaiting licence verification).

    The two lists are kept apart on purpose: a family we have not verified must not be
    reported as if it were in the inventory (an earlier revision of this script did
    exactly that for IRANSansX, which is why the split exists).
    """
    verified: list[str] = []
    for group in (cfg.get("families") or {}).values():
        for entry in group or []:
            if entry.get("name") and entry.get("licence") != "unverified" \
                    and "excluded" not in str(entry.get("status", "")):
                verified.append(str(entry["name"]))
    pending = [str(e["name"]) for e in (cfg.get("pending_licence_verification") or [])
               if e.get("name")]
    for group in (cfg.get("families") or {}).values():
        for entry in group or []:
            if entry.get("name") and (
                entry.get("licence") == "unverified"
                or "excluded" in str(entry.get("status", ""))
            ):
                pending.append(str(entry["name"]))
    return verified, pending


def font_dirs() -> list[str]:
    """Where the fonts come from, for the record. Paths only, no file contents."""
    candidates = [
        Path.home() / ".local" / "share" / "fonts",
        Path.home() / ".fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]
    return [str(p) for p in candidates if p.exists()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit-families", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="print the JSON report only")
    args = ap.parse_args(argv)

    if shutil.which("fc-list") is None:
        print("fc-list not found: install fontconfig to run this scan", file=sys.stderr)
        return 2
    if yaml is None:
        print("PyYAML missing: install it or pass a config-free run", file=sys.stderr)
        return 2

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    inv, pending = inventory_names(cfg)
    families, total = scan(args.limit_families)

    rows: list[dict[str, Any]] = []
    for fam in families:
        base = base_family(fam)
        cls = classify(fam, inv)
        if any(hint.casefold() in base.casefold() for hint in pending):
            cls = "pending_licence"
        row = next((r for r in rows if r["base_family"] == base), None)
        if row is None:
            rows.append({"base_family": base, "class": cls, "names": [fam]})
        else:
            row["names"].append(fam)
    report = {
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host_font_files": total,
        "persian_capable_family_names": len(families),
        "persian_capable_families": len({r["base_family"] for r in rows}),
        "by_class": {c: sum(1 for r in rows if r["class"] == c) for c in (
            "verified_inventory", "free_hint", "proprietary_hint", "pending_licence",
            "unknown")},
        "font_dirs": font_dirs(),
        "inventory_size": len(inv),
        "pending_inventory_size": len(pending),
        "families": rows,
        "caveat": (
            "counts are of family *names* reported by fontconfig; a name is not a licence. "
            "Only configs/content/fonts_v1.yaml carries licence verdicts, each with a sha."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"font files installed      : {total}")
    print(f"Persian-capable families  : {report['persian_capable_families']} "
          f"({len(families)} names)")
    for cls, n in report["by_class"].items():
        print(f"  {cls:<20}: {n}")
    print(f"report                    : {args.out.relative_to(REPO)}")
    print("top families:")
    for row in rows[:12]:
        print(f"  {row['base_family']:<24} {row['class']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
