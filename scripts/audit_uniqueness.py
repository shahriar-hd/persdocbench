"""Audit a generation run for near-duplicate documents (D46 companion).

Usage
-----
    python scripts/audit_uniqueness.py --run data/interim/gen/service-<UTC>
    python scripts/audit_uniqueness.py --run <dir> --threshold 0.8 --json out.json

Reads every ``*.jsonl`` shard under the run (including per-worker ``w*/shards`` and
``accepted-with-flags.jsonl`` — a record seen twice is de-duplicated by ``unit_id``),
reports near-duplicate clusters per register cell, and writes ``uniqueness.md`` and
``uniqueness.json`` inside the run directory.

The script never edits, deletes or regenerates anything: it produces the evidence a
human (or the next generation pass) needs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from persdocbench.content.dedup import (  # noqa: E402
    DEFAULT_PERMUTATIONS,
    DEFAULT_SHINGLES,
    DEFAULT_THRESHOLD,
    UniquenessReport,
    as_markdown,
    audit,
)


def load_records(run: Path, *, include_flagged: bool = False) -> list[dict[str, object]]:
    """Load accepted records once each.

    ``accepted-with-flags.jsonl`` is a *copy* of the same units with soft flags added,
    so it is skipped by default; loading both would double every count.
    """
    paths = sorted(run.glob("w*/shards/shard-*.jsonl")) or sorted(
        run.glob("shards/shard-*.jsonl")
    )
    if include_flagged:
        paths += sorted(run.glob("w*/shards/accepted-with-flags.jsonl"))
        paths += sorted(run.glob("shards/accepted-with-flags.jsonl"))
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                unit_id = str(rec.get("unit_id"))
                if unit_id in seen:
                    continue
                seen.add(unit_id)
                out.append(rec)
    return out


def write_report(run: Path, report: UniquenessReport) -> None:
    (run / "uniqueness.json").write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run / "uniqueness.md").write_text(as_markdown(report) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Near-duplicate audit for a content run")
    ap.add_argument("--run", required=True, help="run directory (under data/interim/gen)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--shingles", type=int, default=DEFAULT_SHINGLES)
    ap.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--include-flagged", action="store_true")
    ap.add_argument(
        "--max-print", type=int, default=12, help="clusters printed to stdout"
    )
    args = ap.parse_args(argv)

    run = Path(args.run)
    if not run.is_dir():
        print(f"not a run directory: {run}", file=sys.stderr)
        return 2
    records = load_records(run, include_flagged=args.include_flagged)
    if not records:
        print(f"no accepted records under {run}", file=sys.stderr)
        return 2

    report = audit(
        records,
        threshold=args.threshold,
        k=args.shingles,
        permutations=args.permutations,
        seed=args.seed,
    )
    write_report(run, report)

    data = report.as_dict()
    print(f"records        : {data['n_records']}")
    print(f"unique         : {data['n_unique_records']}")
    print(
        f"near-duplicate : {data['n_duplicate_records']} "
        f"({data['duplicate_rate']:.2%}) in {data['n_clusters']} cluster(s)"
    )
    print(
        f"threshold      : exact Jaccard >= {args.threshold} on {args.shingles}-word shingles"
    )
    print(f"no shingles    : {data['records_without_shingles']} (short text)")
    near = data["nearest_neighbour_similarity"]
    if near:
        print(
            "nearest pair   : median {median} p90 {p90} p99 {p99} max {max}".format(
                **near
            )
        )
    for pair in data["closest_pairs"][: args.max_print]:
        cell = pair["cell"]
        print(
            f"  closest {pair['similarity']:.3f}  {pair['duplicate']} ~ {pair['keeper']}"
            f"  ({cell['domain']}/{cell['subject_field']}/{cell['register']})"
        )
    for cluster in data["clusters"][: args.max_print]:
        cell = cluster["cell"]
        print(
            f"  {cell['domain']}/{cell['subject_field']}/{cell['register']}: keeper {cluster['keeper']}"
        )
        for dup in cluster["duplicates"]:
            print(f"      ~ {dup['unit_id']}  {dup['similarity']:.3f}")
    if len(data["clusters"]) > args.max_print:
        print(
            f"  … {len(data['clusters']) - args.max_print} more cluster(s) — see uniqueness.md"
        )
    print(f"wrote {run / 'uniqueness.md'} and {run / 'uniqueness.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
