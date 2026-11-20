"""Markdown preview of accepted content units (plaintext preview mode).

User feedback 2026-11-19: the Gate-3 preview should be *markdown/plaintext*,
not rendered pages — visual effects and layouts come later. This script
writes, per accepted unit, one ``.md`` file whose text is the raw content
with blank-line runs collapsed to a single blank line (the renderer used to
turn blank lines into empty paragraphs, which looked like oversized gaps).

Everything shown here is exactly what the content layer produces: ``[FIG: ...]``
placeholders stay visible (a real image is attached at render time), and table
rows stay as ``| ... |`` markdown pipes (a separator row is added for display
only — the renderer skips separator rows anyway).

Usage:
    python scripts/preview_markdown.py --run data/interim/gen/<service> \
        --per-domain 5 --out artifacts/preview/<stamp>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from persdocbench.content.markdown import (  # noqa: E402
    is_separator_row,
    is_table_row,
    separator_for,
    split_row,
)

PRM = yaml.safe_load(
    (ROOT / "configs/content/prompts_v1.yaml").read_text(encoding="utf-8")
)
REG = yaml.safe_load(
    (ROOT / "configs/content/register_v1.yaml").read_text(encoding="utf-8")
)


@dataclass(frozen=True)
class Unit:
    unit_id: str
    domain: str
    register: str
    subject_field: str
    image_slot: bool
    text: str
    words: int


def load_records(run_dir: Path) -> list[Unit]:
    recs: list[dict[str, object]] = []
    for shard in sorted((run_dir / "shards").glob("shard-*.jsonl")):
        recs.extend(
            json.loads(ln)
            for ln in shard.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        )
    if not recs:
        for shard in sorted(run_dir.glob("w*/shards/*.jsonl")):
            recs.extend(
                json.loads(ln)
                for ln in shard.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            )
    if not recs:
        raise SystemExit(f"no accepted records under {run_dir}")
    out: list[Unit] = []
    for r in recs:
        out.append(
            Unit(
                unit_id=str(r["unit_id"]),
                domain=str(r["domain"]),
                register=str(r["register"]),
                subject_field=str(r["subject_field"]),
                image_slot=bool(r.get("image_slot", False)),
                text=str(r["text"]),
                words=int(r.get("words", 0)),
            )
        )
    return out


def clean_text(text: str) -> str:
    """Collapse blank-line runs to a single blank line (spacing fix)."""
    return re.sub(r"\n[ \t]*\n+", "\n\n", text).strip()


def add_table_separators(text: str) -> str:
    """Insert a markdown separator row after the first pipe row of each table.

    Display-only: markdown viewers need a separator to draw a real table;
    the renderer skips separator rows by design.
    """

    def _cells(row: str) -> list[str]:
        return split_row(row)

    lines = text.splitlines()
    out: list[str] = []
    in_table = False
    for i, ln in enumerate(lines):
        st = ln.strip()
        is_row = st.startswith("|") and st.endswith("|")
        if is_row:
            if not in_table:
                # header first, separator second — the reverse order is invalid
                # markdown (observed 2026-11-19: separator landed above header).
                out.append(ln)
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                has_sep = is_table_row(nxt) and is_separator_row(_cells(nxt))
                if not has_sep:
                    out.append(separator_for(_cells(ln)))
                in_table = True
                continue
        else:
            in_table = False
        out.append(ln)
    return "\n".join(out)


def write_preview(unit: Unit, out_dir: Path) -> None:
    dlabel = PRM["domain_labels"].get(unit.domain, {}).get("fa", unit.domain)
    sf = PRM["subject_field_labels"].get(unit.subject_field, unit.subject_field)
    regf = PRM["register_phrasing"][unit.register]["register_fa"]
    te = REG["domains"].get(unit.domain, {}).get("tables_expected", 0)
    body = clean_text(unit.text)
    body = add_table_separators(body)
    md = (
        f"# {dlabel} — {unit.unit_id}\n\n"
        f"| | |\n|---|---|\n"
        f"| حوزهٔ موضوعی | {sf} |\n"
        f"| سطح مخاطب | {regf} |\n"
        f"| تصویر در صفحه | {'بله (image slot)' if unit.image_slot else 'خیر'} |\n"
        f"| جدول مورد انتظار | {'بله' if te else 'خیر'} |\n"
        f"| واژههای بدنه | {unit.words} |\n\n"
        f"---\n\n{body}\n"
    )
    (out_dir / f"{unit.unit_id}.md").write_text(md, encoding="utf-8")


def write_index(units: list[Unit], out_dir: Path, per_domain: int) -> None:
    by_domain: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        by_domain[u.domain].append(u)
    lines = [
        "# پیشنمایش متن محتوای تولیدشده (فقط متن، بدون صفحهآرایی)",
        "",
        "این بسته پیشنمایشِ plaintext است؛ افکتها و صفحهبندی در گام بعدی اعمال میشود.",
        "برچسبهای `[FIG: ...]` در متن، جای تصویر واقعی را در زمان رندر مشخص میکنند.",
        f"{len(units)} واحد پذیرفتهشده، {per_domain} نمونه در هر حوزه.",
        "",
    ]
    for dom in sorted(by_domain):
        dlabel = PRM["domain_labels"].get(dom, {}).get("fa", dom)
        lines.append(f"## {dlabel} ({dom}) — {len(by_domain[dom])}")
        for u in sorted(by_domain[dom], key=lambda x: x.unit_id):
            flags = []
            if u.image_slot:
                flags.append("تصویر")
            if REG["domains"].get(u.domain, {}).get("tables_expected", 0):
                flags.append("جدول")
            tag = "، ".join(flags)
            suffix = f"، {tag})" if tag else ")"
            lines.append(f"- [{u.unit_id}.md]({u.unit_id}.md) ({u.words} واژه{suffix}")
        lines.append("")
    (out_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="content run directory (service or w0)")
    ap.add_argument("--per-domain", type=int, default=5)
    ap.add_argument("--domain", action="append", help="restrict domains (repeatable)")
    ap.add_argument(
        "--out", default=None, help="output dir (default artifacts/preview/<stamp>)"
    )
    args = ap.parse_args(argv)

    run_dir = Path(args.run)
    units = load_records(run_dir)
    if args.domain:
        units = [u for u in units if u.domain in args.domain]
    counts: Counter[str] = Counter()
    chosen: list[Unit] = []
    for u in sorted(units, key=lambda x: (x.domain, x.image_slot, x.unit_id)):
        if counts[u.domain] >= args.per_domain:
            continue
        chosen.append(u)
        counts[u.domain] += 1

    out_dir = Path(args.out) if args.out else ROOT / "artifacts/preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    for u in chosen:
        write_preview(u, out_dir)
    write_index(chosen, out_dir, args.per_domain)

    print(f"preview dir : {out_dir}")
    print(f"units       : {len(chosen)}")
    print(f"domains     : {dict(sorted(counts.items()))}")
    nfig = sum(1 for u in chosen if u.image_slot)
    ntab = sum(
        1 for u in chosen if REG["domains"].get(u.domain, {}).get("tables_expected", 0)
    )
    print(f"image slots : {nfig}  |  table-expected units: {ntab}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
