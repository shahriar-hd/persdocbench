"""Near-duplicate detection for generated content (D46 companion).

Why this module exists
----------------------
``configs/content/generation_v1.yaml`` has declared a dedup contract since Gate 3
(``minhash_shingles: 5``, ``minhash_jaccard_reject: 0.85``) but nothing implemented
it. The 6,000-unit run is planned as 6,000 *unique* documents over 432 register
cells (~14 units per cell), all sharing one prompt template and differing only by
angle and seeded figure slot. That is exactly the regime where a generator starts
repeating itself, so "unique" has to be measured, not assumed.

Design constraints
------------------
* **Standard library only.** The content pipeline runs in a venv that has no
  ``datasketch``/``numpy`` guarantee, and CI installs only the ``dev`` extra. A
  128-permutation MinHash over 5-word shingles is a few hundred hash calls per
  document — negligible at 6,000 units, and it imports nothing.
* **Audit, never rewrite.** This module only *reports* similarity: the caller
  decides whether a near-duplicate is routed to regeneration. Nothing is deleted
  or edited here.
* **Folded text.** Comparison runs on :func:`persdocbench.content.gates.fold_for_matching`,
  so a ZWNJ, an Arabic ``ي`` or a harakat cannot make the same sentence look unique.
  Folding is used for *comparison only* — never for storage.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from persdocbench.content.gates import fold_for_matching

DEFAULT_SHINGLES = 5
DEFAULT_PERMUTATIONS = 128
DEFAULT_THRESHOLD = 0.85  # configs/content/generation_v1.yaml: minhash_jaccard_reject
# Exact Jaccard is computed for any pair whose MinHash estimate reaches this floor,
# so the audit can report the *nearest-neighbour* distribution and not only a
# pass/fail at 0.85. "No duplicates" is a weak claim; "the closest pair in 6,000
# documents is 0.42" is a measurable one.
CANDIDATE_FLOOR = 0.3
TOP_PAIRS = 25
_MERSENNE = (1 << 61) - 1


def shingles(text: str, k: int = DEFAULT_SHINGLES) -> set[str]:
    """Word ``k``-grams of the folded text.

    Returns an empty set for a text shorter than ``k`` words: such a text has no
    shingle evidence and must never be reported as a duplicate *or* as unique by
    shingle comparison — the caller sees ``0.0`` similarity and is expected to
    check ``word_count`` separately.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    tokens = fold_for_matching(text).split()
    if len(tokens) < k:
        return set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _perm_seeds(n: int, seed: int) -> list[tuple[int, int]]:
    """Deterministic ``(a, b)`` pairs for universal hashing over 2**61 - 1."""
    out: list[tuple[int, int]] = []
    material = f"persdocbench-dedup-v1:{seed}".encode()
    for i in range(n):
        digest = hashlib.blake2b(material + struct.pack(">I", i), digest_size=16).digest()
        a = (int.from_bytes(digest[:8], "big") % (_MERSENNE - 1)) + 1
        b = int.from_bytes(digest[8:], "big") % _MERSENNE
        out.append((a, b))
    return out


def _shingle_hash(shingle: str) -> int:
    return (
        int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        % _MERSENNE
    )


def minhash(
    text: str,
    *,
    k: int = DEFAULT_SHINGLES,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 7,
) -> tuple[int, ...]:
    """MinHash signature of ``text``; empty tuple when there are no shingles."""
    parts = shingles(text, k)
    if not parts:
        return ()
    hashed = [_shingle_hash(s) for s in parts]
    sig: list[int] = []
    for a, b in _perm_seeds(permutations, seed):
        sig.append(min(((a * h + b) % _MERSENNE) for h in hashed))
    return tuple(sig)


def jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    """Estimated Jaccard from two equal-length MinHash signatures."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def exact_jaccard(a: set[str], b: set[str]) -> float:
    """Exact shingle Jaccard — for verifying a MinHash estimate, not for 6,000 units."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class DupCluster:
    """One group of records that are near-duplicates of each other."""

    keeper: str
    duplicates: list[tuple[str, float]] = field(default_factory=list)
    cell: tuple[str, str, str] = ("", "", "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "keeper": self.keeper,
            "cell": {
                "domain": self.cell[0],
                "subject_field": self.cell[1],
                "register": self.cell[2],
            },
            "duplicates": [
                {"unit_id": u, "similarity": round(s, 4)} for u, s in self.duplicates
            ],
            "n_duplicates": len(self.duplicates),
        }


@dataclass
class UniquenessReport:
    """Result of one audit pass."""

    n_records: int = 0
    threshold: float = DEFAULT_THRESHOLD
    k: int = DEFAULT_SHINGLES
    permutations: int = DEFAULT_PERMUTATIONS
    n_without_shingles: int = 0
    clusters: list[DupCluster] = field(default_factory=list)
    cells: dict[tuple[str, str, str], int] = field(default_factory=dict)
    nearest: list[float] = field(default_factory=list)
    top_pairs: list[tuple[float, str, str, tuple[str, str, str]]] = field(
        default_factory=list
    )

    @property
    def n_duplicate_records(self) -> int:
        return sum(len(c.duplicates) for c in self.clusters)

    @property
    def n_unique_records(self) -> int:
        return self.n_records - self.n_duplicate_records

    @property
    def duplicate_rate(self) -> float:
        return self.n_duplicate_records / self.n_records if self.n_records else 0.0

    def nearest_quantiles(self) -> dict[str, float]:
        if not self.nearest:
            return {}
        ordered = sorted(self.nearest)

        def q(frac: float) -> float:
            idx = min(len(ordered) - 1, int(frac * len(ordered)))
            return round(ordered[idx], 4)

        return {
            "median": q(0.5),
            "p90": q(0.9),
            "p99": q(0.99),
            "max": round(ordered[-1], 4),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_records": self.n_records,
            "n_unique_records": self.n_unique_records,
            "n_duplicate_records": self.n_duplicate_records,
            "duplicate_rate": round(self.duplicate_rate, 4),
            "threshold": self.threshold,
            "shingles": self.k,
            "permutations": self.permutations,
            "records_without_shingles": self.n_without_shingles,
            "n_clusters": len(self.clusters),
            "nearest_neighbour_similarity": self.nearest_quantiles(),
            "nearest_neighbour_method": (
                f"exact shingle Jaccard over pairs whose MinHash estimate >= {CANDIDATE_FLOOR}; "
                "a closer pair below that floor would need an exhaustive O(n^2) pass"
            ),
            "closest_pairs": [
                {
                    "keeper": a,
                    "duplicate": b,
                    "similarity": round(score, 4),
                    "cell": {"domain": c[0], "subject_field": c[1], "register": c[2]},
                }
                for score, a, b, c in sorted(self.top_pairs, key=lambda t: -t[0])
            ],
            "clusters": [c.as_dict() for c in self.clusters],
            "cells_with_most_records": [
                {"domain": d, "subject_field": f, "register": r, "n": n}
                for (d, f, r), n in sorted(self.cells.items(), key=lambda kv: -kv[1])[:10]
            ],
        }


def audit(
    records: Iterable[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    k: int = DEFAULT_SHINGLES,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 7,
    within_cell_only: bool = True,
    text_key: str = "text",
) -> UniquenessReport:
    """Report near-duplicate clusters over ``records``.

    ``within_cell_only`` compares a record only against earlier records from the same
    ``(domain, subject_field, register)`` cell. That is the regime the config's
    ``minhash_jaccard_reject`` describes (the same cell must not repeat itself) and it
    keeps the pass O(n · records-per-cell) instead of O(n²). Cross-cell similarity is
    deliberately not a defect: a poster and a letter may legitimately share a formula.
    """
    report = UniquenessReport(
        n_records=0, threshold=threshold, k=k, permutations=permutations
    )
    seen: dict[tuple[str, str, str], list[tuple[str, tuple[int, ...], set[str]]]] = {}
    for rec in records:
        report.n_records += 1
        cell = (
            str(rec.get("domain", "")),
            str(rec.get("subject_field", "")),
            str(rec.get("register", "")),
        )
        report.cells[cell] = report.cells.get(cell, 0) + 1
        text = str(rec.get(text_key) or "")
        unit_id = str(rec.get("unit_id", f"#{report.n_records}"))
        sig = minhash(text, k=k, permutations=permutations, seed=seed)
        if not sig:
            report.n_without_shingles += 1
            continue
        key = cell if within_cell_only else ("*", "*", "*")
        bucket = seen.setdefault(key, [])
        dupes: list[tuple[str, str, float]] = []
        best = 0.0
        for other_id, other_sig, other_shingles in bucket:
            score = jaccard(sig, other_sig)
            if score < CANDIDATE_FLOOR:
                continue
            # A MinHash estimate at the threshold is noisy; confirm on the exact
            # shingle sets before calling two pages duplicates. Exact sets are kept
            # only per cell, so the memory cost is bounded by the cell size.
            exact = exact_jaccard(shingles(text, k), other_shingles)
            best = max(best, exact)
            if exact >= CANDIDATE_FLOOR:
                report.top_pairs.append((exact, other_id, unit_id, cell))
            if exact >= threshold:
                # (keeper, duplicate, similarity): the earlier record is the keeper.
                dupes.append((other_id, unit_id, exact))
        if dupes:
            dupes.sort(key=lambda t: -t[2])
            report.clusters.append(
                DupCluster(
                    keeper=dupes[0][0],
                    duplicates=[(unit, score) for _, unit, score in dupes],
                    cell=cell,
                )
            )
        report.nearest.append(best)
        bucket.append((unit_id, sig, shingles(text, k)))
    report.top_pairs = sorted(report.top_pairs, key=lambda t: -t[0])[:TOP_PAIRS]
    return report


def as_markdown(report: UniquenessReport) -> str:
    """Human-readable audit, safe to paste into a gate report."""
    lines = [
        "| metric | value |",
        "|---|---|",
        f"| records audited | {report.n_records} |",
        f"| unique | {report.n_unique_records} |",
        f"| near-duplicate | {report.n_duplicate_records} ({report.duplicate_rate:.2%}) |",
        f"| clusters | {len(report.clusters)} |",
        f"| threshold (exact Jaccard, {report.k}-word shingles) | {report.threshold} |",
        f"| records too short to shingle | {report.n_without_shingles} |",
    ]
    near = report.nearest_quantiles()
    if near:
        lines += [
            "",
            f"Nearest-neighbour similarity (exact Jaccard over MinHash candidates ≥ {CANDIDATE_FLOOR}):",
            "",
            "| nearest-neighbour similarity | value |",
            "|---|---|",
            f"| median | {near['median']} |",
            f"| p90 | {near['p90']} |",
            f"| p99 | {near['p99']} |",
            f"| max (closest pair in the run) | {near['max']} |",
        ]
    if report.clusters:
        lines += ["", "| keeper | duplicate | similarity | cell |", "|---|---|---|---|"]
        for c in report.clusters[:40]:
            for unit, score in c.duplicates:
                lines.append(
                    f"| {c.keeper} | {unit} | {score:.3f} | "
                    f"{c.cell[0]}/{c.cell[1]}/{c.cell[2]} |"
                )
        if len(report.clusters) > 40:
            lines.append(f"| … | {len(report.clusters) - 40} more clusters | | |")
    return "\n".join(lines)
