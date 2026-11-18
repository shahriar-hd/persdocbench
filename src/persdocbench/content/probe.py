"""Model probe: measure a provider catalogue before committing to a model.

Why this exists: choosing a generation model from a leaderboard is choosing from
someone else's prompts on someone else's language. Before a 6,000-request batch we
send a small, fixed probe set to every candidate and measure four things that
actually matter here — **does it answer at all**, **does it answer in Persian**,
**how fast**, **does it clear the content gates**.

Scoring is min-max normalised *within the probed set* and frozen in advance:

    score = 0.35·success + 0.25·persian + 0.20·speed + 0.20·latency

The weights are a stated preference (availability first, language fidelity
second, throughput third), not a fit to results. A model that fails every probe
is not scored at all: it is listed separately with its error class, because
"ranked last" and "did not answer" are different facts.

All measurements are reported with units and are reproducible from
``results.jsonl``.
"""
from __future__ import annotations

import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

PERSIAN_LETTERS = set(
    "اآأإءبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیۀ"
    "يكﻻﻻ"  # Arabic/Persian presentation and Arabic-form letters we still accept
)
ARABIC_DIACRITICS = set("\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670\u0640")

WEIGHTS = {"success": 0.35, "persian": 0.25, "speed": 0.20, "latency": 0.20}


def is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def persian_ratio(text: str) -> float:
    """Share of letters that are Persian/Arabic script (0..1).

    A model that answers in English about Persian content is unusable for this
    project, and this ratio detects it without a language-identification model.
    """
    letters = [ch for ch in text if is_letter(ch)]
    if not letters:
        return 0.0
    persian = sum(1 for ch in letters if ch in PERSIAN_LETTERS)
    return persian / len(letters)


def diacritic_rate(text: str) -> float:
    letters = [ch for ch in text if is_letter(ch)]
    if not letters:
        return 0.0
    return sum(1 for ch in text if ch in ARABIC_DIACRITICS) / len(letters)


@dataclass
class ProbeRow:
    """One (model, probe prompt) measurement. Units are explicit in the names."""

    model: str
    probe: str
    ok: bool
    latency_s: float = 0.0
    completion_tokens: int = 0
    tok_per_s: float = 0.0
    chars: int = 0
    words: int = 0
    persian_ratio: float = 0.0
    gate_verdict: str = ""
    gate_reasons: tuple[str, ...] = ()
    error_class: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "probe": self.probe,
            "ok": self.ok,
            "latency_s": round(self.latency_s, 3),
            "completion_tokens": self.completion_tokens,
            "tok_per_s": round(self.tok_per_s, 2),
            "chars": self.chars,
            "words": self.words,
            "persian_ratio": round(self.persian_ratio, 4),
            "gate_verdict": self.gate_verdict,
            "gate_reasons": list(self.gate_reasons),
            "error_class": self.error_class,
            "error": self.error,
        }


@dataclass
class ModelSummary:
    model: str
    probes: int
    answered: int
    success_rate: float
    median_latency_s: float
    median_tok_per_s: float
    mean_persian_ratio: float
    gate_pass_rate: float
    ban_rate: float
    error_classes: list[str] = field(default_factory=list)
    score: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "probes": self.probes,
            "answered": self.answered,
            "success_rate": round(self.success_rate, 4),
            "median_latency_s": round(self.median_latency_s, 3),
            "median_tok_per_s": round(self.median_tok_per_s, 2),
            "mean_persian_ratio": round(self.mean_persian_ratio, 4),
            "gate_pass_rate": round(self.gate_pass_rate, 4),
            "ban_rate": round(self.ban_rate, 4),
            "error_classes": sorted(set(self.error_classes)),
            "score": round(self.score, 4),
        }


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def summarise(rows: Iterable[ProbeRow], banned_hits: dict[tuple[str, str], int] | None = None) -> list[ModelSummary]:
    """Aggregate per model. ``banned_hits`` maps (model, probe) → count of banned hits."""
    banned_hits = banned_hits or {}
    by_model: dict[str, list[ProbeRow]] = {}
    for row in rows:
        by_model.setdefault(row.model, []).append(row)

    out: list[ModelSummary] = []
    for model, rs in by_model.items():
        good = [r for r in rs if r.ok]
        hits = sum(banned_hits.get((r.model, r.probe), 0) for r in rs)
        out.append(
            ModelSummary(
                model=model,
                probes=len(rs),
                answered=len(good),
                success_rate=len(good) / len(rs) if rs else 0.0,
                median_latency_s=_median([r.latency_s for r in good]),
                median_tok_per_s=_median([r.tok_per_s for r in good]),
                mean_persian_ratio=(sum(r.persian_ratio for r in good) / len(good)) if good else 0.0,
                gate_pass_rate=(sum(1 for r in good if r.gate_verdict == "accept") / len(good)) if good else 0.0,
                ban_rate=(hits / len(rs)) if rs else 0.0,
                error_classes=[r.error_class for r in rs if not r.ok],
            )
        )
    return out


def _norm(values: list[float], *, higher_is_better: bool) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0 if v > 0 else 0.0 for v in values]
    if higher_is_better:
        return [(v - lo) / (hi - lo) for v in values]
    return [(hi - v) / (hi - lo) for v in values]


def rank(summaries: Sequence[ModelSummary]) -> tuple[list[ModelSummary], list[ModelSummary]]:
    """Score and sort. Returns (ranked, unavailable).

    Unavailable = zero successful probes. They are excluded from scoring so their
    dead rows cannot shift the normalisation of live models.
    """
    live = [s for s in summaries if s.answered > 0]
    dead = [s for s in summaries if s.answered == 0]
    if live:
        success = [s.success_rate for s in live]
        persian = [s.mean_persian_ratio for s in live]
        speed = [s.median_tok_per_s for s in live]
        latency = [s.median_latency_s for s in live]
        n_success = _norm(success, higher_is_better=True)
        n_persian = _norm(persian, higher_is_better=True)
        n_speed = _norm(speed, higher_is_better=True)
        n_latency = _norm(latency, higher_is_better=False)
        for i, s in enumerate(live):
            s.score = (
                WEIGHTS["success"] * n_success[i]
                + WEIGHTS["persian"] * n_persian[i]
                + WEIGHTS["speed"] * n_speed[i]
                + WEIGHTS["latency"] * n_latency[i]
            )
    ranked = sorted(live, key=lambda s: (-s.score, -s.success_rate, s.median_latency_s, s.model))
    return ranked, sorted(dead, key=lambda s: s.model)


def to_markdown(ranked: Sequence[ModelSummary], dead: Sequence[ModelSummary], *, probe_count: int) -> str:
    lines = [
        "# NVIDIA endpoint model probe",
        "",
        f"Probes per model: **{probe_count}** · weights: "
        f"success {WEIGHTS['success']}, persian {WEIGHTS['persian']}, "
        f"speed {WEIGHTS['speed']}, latency {WEIGHTS['latency']}",
        "",
        "Scores are min-max normalised **within this probe run**, so they rank *these* "
        "candidates against each other. They are not absolute quality and not comparable "
        "with another run that probed a different set.",
        "",
        "## Ranked",
        "",
        "| # | model | score | success | persian ratio | med. latency (s) | med. tok/s | gate pass |",
        "|---|-------|-------|---------|---------------|------------------|------------|-----------|",
    ]
    for i, s in enumerate(ranked, 1):
        lines.append(
            f"| {i} | `{s.model}` | {s.score:.3f} | {s.success_rate:.0%} ({s.answered}/{s.probes}) | "
            f"{s.mean_persian_ratio:.2f} | {s.median_latency_s:.2f} | {s.median_tok_per_s:.1f} | "
            f"{s.gate_pass_rate:.0%} |"
        )
    if dead:
        lines += [
            "",
            "## Did not answer (not scored)",
            "",
            "| model | error class |",
            "|-------|-------------|",
        ]
        for s in dead:
            lines.append(f"| `{s.model}` | {', '.join(sorted(set(s.error_classes))) or 'n/v'} |")
    return "\n".join(lines) + "\n"