"""Build a review pack from an accepted-content run: sample, preview, render, REVIEW.md.

    python scripts/build_review_pack.py --run data/interim/gen/service-<UTC> \\
        --out artifacts/review/<UTC> --per-domain 5 [--dpi 110]

The pack is the artefact a human reviewer signs off on (D39/D46). It contains, for a
deterministic per-domain sample:

* ``content/shard-0000.jsonl`` — the sampled records, byte-for-byte as stored;
* ``preview/`` — one markdown file per unit plus ``index.md``;
* ``render/`` — one PDF + PNG per unit, ``bboxes/``, ``overlays/``;
* ``REVIEW.md`` — the per-unit table a reviewer ticks, with the two automated checks
  (one-page fit, label leak) and the counts they can verify against ``REVIEW.md``.

Rendering needs the ``render`` extra, so this is a script and not a test.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PYTHON = sys.executable


def load_accepted(run: Path) -> list[dict]:
    """Accepted records from every worker, keyed by unit id (first writer wins)."""
    seen: dict[str, dict] = {}
    for path in sorted(run.glob("w*/shards/accepted-with-flags.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                seen.setdefault(rec["unit_id"], rec)
    for path in sorted(run.glob("w*/shards/shard-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                seen.setdefault(rec["unit_id"], rec)
    return sorted(seen.values(), key=lambda r: (r["domain"], r["unit_id"]))


def sample(records: list[dict], per_domain: int, seed: int) -> list[dict]:
    """Spread the sample over each domain instead of taking its first N units."""
    by_domain: dict[str, list[dict]] = collections.defaultdict(list)
    for rec in records:
        by_domain[rec["domain"]].append(rec)
    rng = random.Random(seed)
    picked: list[dict] = []
    for domain in sorted(by_domain):
        bag = by_domain[domain]
        if len(bag) <= per_domain:
            picked.extend(bag)
            continue
        if per_domain == 1:
            idx = [0]
        else:
            idx = sorted(
                {0, len(bag) - 1}
                | set(rng.sample(range(1, len(bag) - 1), max(0, per_domain - 2)))
            )
        picked.extend(bag[i] for i in idx[:per_domain])
    return picked


def run(cmd: list[str]) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def review_markdown(
    pack: Path,
    render: Path,
    records: list[dict],
    source_run: Path,
    label_report: dict | None,
) -> str:
    manifest = json.loads((render / "render_manifest.json").read_text(encoding="utf-8"))
    by_unit = {u["unit_id"]: u for u in manifest["units"]}
    domain_fa = yaml.safe_load(
        (ROOT / "configs/content/prompts_v1.yaml").read_text(encoding="utf-8")
    )["domain_labels"]
    counts = collections.Counter(r["domain"] for r in records)
    one_page = sum(1 for u in manifest["units"] if u["one_page_ok"])
    lines = [
        "# PersDocBench — review pack",
        "",
        f"- source run: `{source_run.name}` · sampled records: `content/shard-0000.jsonl`",
        f"- different unit ids: **{len({r['unit_id'] for r in records})}** · records: {len(records)}",
        f"- markdown: `{pack.name}/preview/index.md` · images: `{pack.name}/render/pages/` · PDFs: `{pack.name}/render/pdfs/`",
        f"- **one page per document (D45): {one_page}/{len(manifest['units'])}**",
        "",
        "Overlays: **red** = text line · **blue** = figure · **green** = table (render-time only, never GT; D38).",
        "",
    ]
    if label_report is not None:
        hard = label_report["scaffold_hits"]
        lines += [
            "## Automated checks",
            "",
            f"- label leak (`scripts/check_render_labels.py`): **{len(hard)} page(s)** where a block starts with one of the unit's own element labels + a separator. "
            f"({label_report['n_labels']} labels across {label_report['n_domains']} domains were checked; "
            f"{len(label_report['informational'])} page(s) use such a word as ordinary document text — expected, e.g. «روش» as a paper heading.)",
            f"- one page per document: **{one_page}/{len(manifest['units'])}**",
            "",
        ]
        if hard:
            lines += [
                "Leaks found:",
                "",
                "```json",
                json.dumps(hard[:20], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    lines += [
        "## Units by domain",
        "",
        "| domain | units | samples | academic | fit | ok |",
        "|---|---|---|---|---|---|",
    ]
    for domain in sorted(counts):
        fa = domain_fa.get(domain, {}).get("fa") or domain
        samples = sum(1 for r in records if r["domain"] == domain)
        units = sorted(u for u in by_unit if u.rsplit("_", 1)[0] == domain)
        pt = ", ".join(f"{by_unit[u]['fit_font_pt']:g}" for u in units) or "–"
        lines.append(f"| {fa} (`{domain}`) | {counts[domain]} | {samples} | | {pt}pt | |")
    lines += ["", "## Per-unit detail", ""]
    for domain in sorted(counts):
        fa = domain_fa.get(domain, {}).get("fa") or domain
        lines += [
            f"### {fa} — `{domain}`",
            "",
            "| unit | field | register | img | pt | fit | lines | figs | tables |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for rec in [r for r in records if r["domain"] == domain]:
            unit = by_unit.get(rec["unit_id"])
            if unit is None:
                lines.append(
                    f"| {rec['unit_id']} | {rec['subject_field']} | {rec['register']} | – | – | not rendered | – | – | – |"
                )
                continue
            page = unit["pages"][0]
            lines.append(
                f"| {rec['unit_id']} | {rec['subject_field']} | {rec['register']} "
                f"| {'Y' if rec.get('image_slot') else '–'} | {unit['fit_font_pt']:g} "
                f"| {unit['fit_attempts']} {'ok' if unit['one_page_ok'] else 'OVERFLOW'} "
                f"| {page['lines']} | {page['figures']} | {page['tables']} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="build a human review pack")
    ap.add_argument("--run", required=True, help="accepted-content run directory")
    ap.add_argument("--out", required=True, help="pack directory to create")
    ap.add_argument("--per-domain", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--font", default=None)
    args = ap.parse_args(argv)

    source_run = Path(args.run)
    pack = Path(args.out)
    if (pack / "REVIEW.md").exists():
        print(f"{pack}/REVIEW.md already exists; refusing to overwrite", file=sys.stderr)
        return 2
    records = load_accepted(source_run)
    if not records:
        print(f"no accepted records under {source_run}", file=sys.stderr)
        return 2
    picked = sample(records, args.per_domain, args.seed)
    # The single-``shards/`` layout (no w<k>/ level) is what both preview and render
    # accept for a run that was not produced by the worker service.
    content = pack / "content"
    shards = content / "shards"
    shards.mkdir(parents=True, exist_ok=True)
    with (shards / "shard-0000.jsonl").open("w", encoding="utf-8") as fh:
        for rec in picked:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    source_manifest = next(iter(sorted(source_run.glob("w*/manifest.json"))), None)
    config_sha = None
    if source_manifest:
        config_sha = json.loads(source_manifest.read_text(encoding="utf-8")).get(
            "config_sha256"
        )
    (content / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "review-pack",
                "source_run": str(source_run),
                "output_format": "json",
                "config_sha256": config_sha,
                "units_planned": len(picked),
                "workers": 1,
                "worker_index": 0,
                "note": "human review pack, not a generation run",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    preview = pack / "preview"
    render = pack / "render"
    run(
        [
            PYTHON,
            "scripts/preview_markdown.py",
            "--run",
            str(content),
            "--out",
            str(preview),
        ]
    )
    cmd = [
        PYTHON,
        "scripts/render_samples.py",
        "--run",
        str(content),
        "--out",
        str(render),
        "--dpi",
        str(args.dpi),
    ]
    if args.font:
        cmd += ["--font", args.font]
    run(cmd)
    label_report = None
    check = subprocess.run(
        [
            PYTHON,
            "scripts/check_render_labels.py",
            "--render",
            str(render),
            "--json",
            str(render / "label_check.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(check.stdout.strip())
    if (render / "label_check.json").exists():
        label_report = json.loads(
            (render / "label_check.json").read_text(encoding="utf-8")
        )
    elif check.returncode != 0 and "pymupdf" in check.stderr:
        print(
            "label check skipped: PyMuPDF missing (install the render extra)",
            file=sys.stderr,
        )
    (pack / "REVIEW.md").write_text(
        review_markdown(pack, render, picked, source_run, label_report), encoding="utf-8"
    )
    print(f"\npack: {pack}")
    print(
        f"  REVIEW.md   {len(picked)} documents / {len({r['domain'] for r in picked})} domain(s)"
    )
    print("  content/    shards/shard-0000.jsonl")
    print("  preview/    markdown per unit")
    print("  render/     pdfs, pages, bboxes, overlays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
