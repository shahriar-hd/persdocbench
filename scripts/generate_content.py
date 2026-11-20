#!/usr/bin/env python3
"""Generate Persian content units: plan -> dispatch -> gate -> store.

Three jobs, deliberately separated:

1. **plan** — decide *which* units to ask for, deterministically, from the register
   profile (domain × subject_field × register shares, largest-remainder allocation).
   The plan is written to disk before any request is sent, so a run is reproducible
   and a reviewer can see what was asked for even if the batch dies.
2. **dispatch** — hand requests to ``Dispatcher``, which owns the per-key token
   buckets, retries and the append-only ledger. Nothing here sleeps on a 429.
3. **store** — accepted text goes to append-only shards; rejects go to
   ``rejects.jsonl`` with their gate report. Nothing is ever edited in place, and no
   rejected sample is deleted: a 30 % reject rate is a finding about the generator.

Model fall-through
------------------
``Disaster`` in a 6,000-request batch is a single model id that stops answering
(measured: 2 of 18 probed models answered in every sweep). The runner therefore runs
rounds over ``provider.model_candidates``: a request that failed with a
*model-shaped* error is re-issued on the next candidate. Content rejections never
fall through — a model that writes lay prose must not be replaced, it must be
recorded.

Usage
-----
    python scripts/generate_content.py --dry-run            # write the plan, no network
    python scripts/generate_content.py --limit 8            # smoke batch
    python scripts/generate_content.py                      # planned batch
    python scripts/generate_content.py --resume             # continue an interrupted run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from persdocbench.content.dispatcher import Dispatcher, Request, Response  # noqa: E402
from persdocbench.content.dotenv import load_dotenv  # noqa: E402
from persdocbench.content.gates import (  # noqa: E402
    body_text,
    mean_words_per_sentence,
    numeric_density,
    prose_lines,
    terminology_density,
    text_sha256,
    word_count,
)
from persdocbench.content.keys import KeyPool, parse_keys  # noqa: E402
from persdocbench.content.ledger import Ledger  # noqa: E402
from persdocbench.content.nvidia import (  # noqa: E402
    ModelForbidden,
    ModelUnavailable,
    ModelUnsupported,
    NvidiaClient,
    ParamRejected,
)

GEN = ROOT / "configs" / "content" / "generation_v1.yaml"
REG = ROOT / "configs" / "content" / "register_v1.yaml"
PRM = ROOT / "configs" / "content" / "prompts_v1.yaml"

# Errors that mean "this model id cannot serve this request", not "the content is bad".
MODEL_ERRORS = (ModelForbidden, ModelUnavailable, ModelUnsupported, ParamRejected)


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Unit:
    """One content unit to be generated. Deterministic given the run seed."""

    unit_id: str
    domain: str
    subject_field: str
    register: str
    digit_style: str
    angle: str
    image_slot: bool = False  # D37: unit text carries a [FIG: ...] placeholder

    def meta(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "domain": self.domain,
            "subject_field": self.subject_field,
            "register": self.register,
            "digit_style": self.digit_style,
            "angle": self.angle,
            "image_slot": self.image_slot,
        }


def _largest_remainder(total: int, shares: dict[str, float]) -> dict[str, int]:
    """Allocate ``total`` integer slots by share without drift.

    Rounding each share independently makes a 1,600-unit run miss its own spec; the
    remainder is therefore handed to the largest fractional parts, deterministically
    (ties broken by key name) so the same config always produces the same plan.
    """
    exact = {k: total * v for k, v in shares.items()}
    out = {k: int(v) for k, v in exact.items()}
    left = total - sum(out.values())
    order = sorted(exact, key=lambda k: (-(exact[k] - out[k]), k))
    for k in order[:left]:
        out[k] += 1
    return out


def build_plan(
    *,
    count: int,
    seed: int,
    reg: dict[str, Any],
    prm: dict[str, Any],
    domains: Iterable[str] | None = None,
    subject_fields: Iterable[str] | None = None,
    register: str | None = None,
) -> list[Unit]:
    """Allocate ``count`` units across domain × subject_field, then assign registers."""
    rng = random.Random(seed)
    dom_shares = {
        d: float(v.get("share", 1.0 / len(reg["domains"])))
        for d, v in reg["domains"].items()
    }
    if domains:
        keep = set(domains)
        dom_shares = {d: s for d, s in dom_shares.items() if d in keep}
        tot = sum(dom_shares.values())
        dom_shares = {d: s / tot for d, s in dom_shares.items()}
    if not dom_shares:
        raise SystemExit("no domains selected")

    sf_shares = {k: float(v["share"]) for k, v in reg["subject_fields"].items()}
    if subject_fields:
        keep = set(subject_fields)
        sf_shares = {s: w for s, w in sf_shares.items() if s in keep}
        tot = sum(sf_shares.values())
        sf_shares = {s: w / tot for s, w in sf_shares.items()}
    if not sf_shares:
        raise SystemExit("no subject fields selected")

    per_domain = _largest_remainder(count, dom_shares)
    digit_styles = {k: float(v["weight"]) for k, v in prm["digit_styles"].items()}
    angle_ids = [a["id"] for a in prm["angles"]]
    reg_mix_global = {k: float(v["share"]) for k, v in reg["registers"].items()}

    units: list[Unit] = []
    for domain, n in per_domain.items():
        if n == 0:
            continue
        mix = reg["domains"][domain].get("register_mix") or reg_mix_global
        reg_alloc = _largest_remainder(n, mix)
        if register:  # forced register (calibration runs need a single tier)
            reg_alloc = {k: (n if k == register else 0) for k in reg_alloc}
        # A subject field is only eligible in the domains that host it: an
        # engineering vocabulary inside a literary page produced exactly that
        # mismatch on the first live batch (a "book_literary" unit written about
        # pump calibration), which is a planning bug, not a model failure.
        eligible = {
            f: w
            for f, w in sf_shares.items()
            if not reg["subject_fields"][f].get("host_domains")
            or domain in reg["subject_fields"][f]["host_domains"]
        } or sf_shares
        tot = sum(eligible.values())
        eligible = {f: w / tot for f, w in eligible.items()}
        sf_alloc = _largest_remainder(n, eligible)
        filled: list[str] = []
        for reg_name, k in reg_alloc.items():
            filled += [reg_name] * k
        rng.shuffle(filled)
        cells: list[str] = []
        for sf, k in sf_alloc.items():
            cells += [sf] * k
        rng.shuffle(cells)
        for i in range(n):
            units.append(
                Unit(
                    unit_id=f"{domain}_{i:04d}",
                    domain=domain,
                    subject_field=cells[i],
                    register=filled[i],
                    digit_style=rng.choices(
                        list(digit_styles), weights=list(digit_styles.values())
                    )[0],
                    angle=rng.choice(angle_ids),
                    image_slot=rng.random()
                    < float(reg["domains"][domain].get("image_share", 0.0)),
                )
            )
    units.sort(key=lambda u: u.unit_id)
    return units


def partition(units: list[Unit], *, workers: int, index: int) -> list[Unit]:
    """Deterministic slice of the plan for one worker in a multi-process run.

    Hashing the ``unit_id`` instead of slicing ``units[i::n]`` means a worker's
    assignment does not depend on how many other workers were alive when the plan was
    built, so a worker can be restarted alone without reshuffling its neighbours.
    """
    if workers <= 1:
        return units
    return [
        u
        for u in units
        if int(hashlib.sha1(u.unit_id.encode()).hexdigest(), 16) % workers == index
    ]


# --------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------
def build_prompt(unit: Unit, reg: dict[str, Any], prm: dict[str, Any]) -> tuple[str, str]:
    """Return (system, user) for one unit. No randomness: same unit, same prompt."""
    domain = reg["domains"][unit.domain]
    dlabel = prm["domain_labels"][unit.domain]
    rphrase = prm["register_phrasing"][unit.register]
    sf_label = prm["subject_field_labels"][unit.subject_field]
    lex = list(reg["subject_fields"][unit.subject_field].get("lexicon_seed", []))
    banned = list(reg["banned_patterns"].get("fa", [])) + list(
        reg["banned_patterns"].get("definition_copulas_fa", [])
    )
    elements = "، ".join(
        dlabel["element_labels"].get(e, e) for e in domain["required_elements"]
    )
    angle = next(a["fa"] for a in prm["angles"] if a["id"] == unit.angle)
    length = prm["length"]

    system = prm["system"].format(
        domain_fa=dlabel["fa"],
        subject_field_fa=sf_label,
        register_fa=rphrase["register_fa"],
    )
    figure_line = prm.get("figure_instruction", "") if unit.image_slot else ""
    table_line = (
        prm.get("table_instruction", "") if domain.get("tables_expected", 0) else ""
    )
    user = prm["user"].format(
        domain_fa=dlabel["fa"],
        subject_field_fa=sf_label,
        register_fa=rphrase["register_fa"],
        reader_assumes_fa=rphrase["reader_assumes_fa"],
        defines_terms_line=rphrase["defines_terms_line"],
        elements_fa=elements,
        body_words_min=length["body_words_min"],
        body_words_max=length["body_words_max"],
        target_min=length.get("target_min", length["body_words_min"]),
        numeric_policy_fa=prm["numeric_policy_phrasing"][domain["numeric_policy"]],
        digit_style_fa=prm["digit_styles"][unit.digit_style]["label_fa"],
        angle_fa=angle,
        figure_line=figure_line,
        table_line=table_line,
        lexicon_hint="، ".join(lex) if lex else "—",
        banned_hint="؛ ".join(banned[:14]),
    )
    return system, user


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
@dataclass
class Writer:
    """Append-only writer for accepted units, sharded by count."""

    out_dir: Path
    shard_size: int
    accepted: list[dict[str, object]] = field(default_factory=list)
    rejects: list[dict[str, object]] = field(default_factory=list)
    _shard: int = 0
    _in_shard: int = 0

    def __post_init__(self) -> None:
        (self.out_dir / "shards").mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        return self.out_dir / "shards" / f"shard-{self._shard:04d}.jsonl"

    def accept(self, record: dict[str, object]) -> None:
        if self._in_shard >= self.shard_size:
            self._shard += 1
            self._in_shard = 0
        with self._path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._in_shard += 1
        self.accepted.append(record)

    def reject(self, record: dict[str, object]) -> None:
        with (self.out_dir / "rejects.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self.rejects.append(record)


def _config_hashes() -> dict[str, str]:
    out = {}
    for p in (GEN, REG, PRM):
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _read_env() -> None:
    # A launch-injected key must win over a stale .env, so load_dotenv is
    # non-overriding by contract (see dotenv.py).
    env_file = ROOT / ".env"
    if env_file.is_file():
        load_dotenv(env_file)


async def dispatch(
    *,
    units: list[Unit],
    models: list[str],
    gen: dict[str, Any],
    reg: dict[str, Any],
    prm: dict[str, Any],
    out_dir: Path,
    concurrency: int,
    max_attempts: int,
    resume: bool,
    request_timeout: float = 180.0,
    rpm_override: float | None = None,
) -> dict[str, object]:
    pool = KeyPool.from_env(
        gen["provider"]["key_env"],
        rate_per_minute=float(
            rpm_override or gen["provider"]["requests_per_minute_per_key"]
        ),
        burst=int(gen["provider"]["burst_per_key"]),
        stagger_seconds=float(gen["dispatch"].get("stagger_seconds", 0.0)),
    )
    client = NvidiaClient(base_url=gen["provider"]["base_url"], timeout=request_timeout)
    ledger = Ledger(out_dir / "ledger.jsonl")
    writer = Writer(out_dir=out_dir, shard_size=int(gen["dispatch"]["shard_size"]))

    params = {
        "temperature": gen["generation"]["temperature"],
        "top_p": gen["generation"]["top_p"],
        "max_tokens": gen["generation"]["max_tokens"],
    }
    # register_v1 thresholds are the single source; generation_v1 points at them
    banned = list(reg["banned_patterns"]["fa"]) + list(
        reg["banned_patterns"]["definition_copulas_fa"]
    )

    def make(unit: Unit, model: str) -> Request:
        system, user = build_prompt(unit, reg, prm)
        return Request(
            prompt=user,
            model=model,
            params={
                **params,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            variant=unit.unit_id,
            meta={**unit.meta(), "system_sha256": text_sha256(system)},
        )

    def on_reject(req: Request, resp: Response, report: Any) -> None:
        """Rejected text is evidence, not noise: calibration of the hard gates
        requires seeing what was rejected. Earlier batches stored only the reason;
        that made it impossible to tell a conversational preamble ("حتما، ...")
        from a legitimate mid-text adverb, so the gate could not be tuned."""
        writer.reject(
            {
                "unit_id": req.meta["unit_id"],
                "request_id": req.id,
                "model": req.model,
                "domain": req.meta["domain"],
                "subject_field": req.meta["subject_field"],
                "register": req.meta["register"],
                "digit_style": req.meta["digit_style"],
                "angle": req.meta["angle"],
                "image_slot": req.meta["image_slot"],
                "reason": ",".join(report.reasons),
                "gate": report.as_dict(),
                "text": resp.text,
                "text_sha256": text_sha256(resp.text),
                "usage": resp.usage,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "provenance": "synthetic_native",
            }
        )

    def on_ok(req: Request, resp: Response, report: Any) -> None:
        wc = report.words
        writer.accept(
            {
                "unit_id": req.meta["unit_id"],
                "request_id": req.id,
                "model": req.model,
                "domain": req.meta["domain"],
                "subject_field": req.meta["subject_field"],
                "register": req.meta["register"],
                "digit_style": req.meta["digit_style"],
                "angle": req.meta["angle"],
                "image_slot": req.meta["image_slot"],
                "text": resp.text,
                "text_sha256": text_sha256(resp.text),
                "words": wc,
                "gate": report.as_dict(),
                "usage": resp.usage,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "provenance": "synthetic_native",
                "generator": "scripts/generate_content.py",
            }
        )

    rounds: list[dict[str, object]] = []
    remaining = {u.unit_id: u for u in units}

    for model in models:
        if not remaining:
            break
        reqs = []
        for unit in remaining.values():
            r = make(unit, model)
            if resume and ledger.is_terminal(r.id):
                # already answered in an earlier run: keep whatever it produced
                remaining.pop(unit.unit_id)
                continue
            reqs.append(r)
        if not reqs:
            continue

        disp = Dispatcher(
            client,
            ledger,
            pool,
            concurrency=concurrency,
            max_attempts=max_attempts,
            backoff_base=float(gen["dispatch"]["backoff_base"]),
            backoff_cap=float(gen["dispatch"]["backoff_cap"]),
            banned=banned,
            # per-register thresholds are applied after the fact: one Dispatcher run
            # spans registers, so the strictest value would reject the loosest tier
            on_ok=on_ok,
            on_reject=on_reject,
        )
        stats = await disp.run(reqs)
        rounds.append({"model": model, "requests": len(reqs), **stats.summary()})

        # Which units are still eligible for the next model?
        next_remaining: dict[str, Unit] = {}
        for unit in remaining.values():
            r = make(unit, model)
            rec = ledger.get(r.id)
            if rec is None:
                next_remaining[unit.unit_id] = unit
                continue
            if rec.status.value in ("ok", "rejected"):
                continue  # answered, or answered badly: never falls through
            err = rec.error or ""
            if any(
                name in err
                for name in (
                    "ModelForbidden",
                    "ModelUnavailable",
                    "ModelUnsupported",
                    "ParamRejected",
                    "fatal:",
                    "404",
                    "400",
                    "422",
                )
            ):
                next_remaining[unit.unit_id] = unit
            else:
                # transport failure after max_attempts: keep for a later --resume
                pass
        remaining = next_remaining

    return {
        "rounds": rounds,
        "accepted": len(writer.accepted),
        "rejected": len(writer.rejects),
        "keys": pool.snapshot(),
        "ledger": ledger.counts(),
        "slept_seconds": round(pool.slept_seconds, 1),
    }


def recompute_metrics(
    record: dict[str, object], reg: dict[str, Any], prm: dict[str, Any]
) -> dict[str, object]:
    """Re-measure one accepted record against its *own* register and lexicon.

    The dispatcher runs one gate for a whole batch, so it cannot know which subject
    field a text belongs to; measuring there leaves terminology density at 0.0 and
    flags every unit. Measured here per record instead, with the lexicon of its
    subject field, and with R5 read off body prose rather than table rows.
    """
    text = str(record.get("text") or "")
    field = str(record.get("subject_field"))
    lex = reg["subject_fields"].get(field, {}).get("lexicon_seed", [])
    body = body_text(text)
    gate = dict(record.get("gate") or {})
    gate["term_density"] = round(terminology_density(text, lex), 4)
    gate["num_density"] = round(numeric_density(text), 4)
    gate["mean_sentence_words"] = round(mean_words_per_sentence(prose_lines(text)), 2)
    gate["words"] = word_count(text)
    gate["words_body"] = word_count(body)
    gate["lexicon_size"] = len(lex)
    gate["lexicon"] = field
    gate["measured_on"] = "body_text (tables/headings excluded from R5)"
    record["gate"] = gate
    record["words"] = gate["words"]
    return record


def apply_register_thresholds(
    records: list[dict[str, object]],
    reg: dict[str, Any],
    prm: dict[str, Any] | None = None,
) -> None:
    """Stamp each accepted record with the *soft* threshold verdict for its register.

    R1/R3/R5 are reported, not enforced: a threshold nobody validated must route a
    sample to human review, never delete it. The Gate-3 pilot calibrates these
    numbers; until then they are advisory and labelled as such.
    """
    r1 = reg["checks"]["R1_terminology_density"]["min"]
    r5 = reg["checks"]["R5_complexity"]
    r3 = reg["checks"]["R3_numeric_density"]["min_by_policy"]
    lo = float(prm["length"]["body_words_min"]) if prm else 0.0
    hi = float(prm["length"]["body_words_max"]) if prm else 0.0
    for rec in records:
        gate = dict(rec.get("gate") or {})
        tier = str(rec.get("register"))
        need = float(r1.get(tier, 0))
        # numeric_density is already expressed per 80 words, the same unit the
        # threshold is stated in; the previous comparison multiplied it by 80 again
        # and flagged every sample in the batch.
        domain_cfg = reg["domains"].get(str(rec.get("domain")), {})
        need_num = float(r3.get(domain_cfg.get("numeric_policy", "moderate"), 0.0))
        flags = []
        if gate.get("term_density", 0.0) < need:
            flags.append("R1_below_min")
        if not (r5["min"] <= gate.get("mean_sentence_words", 0) <= r5["max"]):
            flags.append("R5_out_of_band")
        if lo and not (lo * 0.9 <= gate.get("words_body", 0) <= hi * 1.1):
            flags.append("length_out_of_band")
        if need_num and gate.get("num_density", 0.0) < need_num:
            flags.append("R3_below_min")
        gate["soft_flags"] = flags
        gate["thresholds_source"] = "configs/content/register_v1.yaml (uncalibrated)"
        rec["gate"] = gate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="units to plan (default: units.content_units_core from the spec)",
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="hard cap on dispatched units"
    )
    ap.add_argument("--domain", action="append", help="restrict to a domain (repeatable)")
    ap.add_argument(
        "--subject-field", action="append", help="restrict to a subject field"
    )
    ap.add_argument("--register", default=None, help="force one register tier")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default=None, help="override the candidate list")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--out", default=None, help="run directory (default: stamped)")
    ap.add_argument(
        "--resume", action="store_true", help="skip requests already terminal"
    )
    ap.add_argument(
        "--request-timeout",
        type=float,
        default=180.0,
        help="per-request read timeout in seconds (default 180)",
    )
    ap.add_argument(
        "--rpm",
        type=float,
        default=None,
        help="override requests/minute/key (the service uses this)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="total workers in this run; with --worker-index, plan a slice",
    )
    ap.add_argument(
        "--worker-index", type=int, default=0, help="0-based index of this worker"
    )
    ap.add_argument("--dry-run", action="store_true", help="write the plan, send nothing")
    args = ap.parse_args(argv)

    gen = yaml.safe_load(GEN.read_text(encoding="utf-8"))
    reg = yaml.safe_load(REG.read_text(encoding="utf-8"))
    prm = yaml.safe_load(PRM.read_text(encoding="utf-8"))

    count = args.count
    if count is None:
        bench = yaml.safe_load(
            (ROOT / "configs" / "benchmark" / "benchmark_v1.yaml").read_text(
                encoding="utf-8"
            )
        )
        count = int(bench["units"]["content_units_core"])
    if args.limit:
        count = min(count, args.limit)

    units = build_plan(
        count=count,
        seed=args.seed,
        reg=reg,
        prm=prm,
        domains=args.domain,
        subject_fields=args.subject_field,
        register=args.register,
    )
    if args.workers > 1:
        if not 0 <= args.worker_index < args.workers:
            raise SystemExit("--worker-index must be in [0, --workers)")
        units = partition(units, workers=args.workers, index=args.worker_index)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "data" / "interim" / "gen" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    models = [args.model] if args.model else list(gen["provider"]["model_candidates"])
    never = set(gen["provider"].get("never_use") or [])
    clash = [m for m in models if m in never]
    if clash:
        raise SystemExit(f"model(s) in provider.never_use: {clash}")

    # plan first, always: a run that dies must still say what it asked for
    plan_rows = []
    for unit in units:
        system, user = build_prompt(unit, reg, prm)
        plan_rows.append(
            {
                **unit.meta(),
                "model_candidates": models,
                "system_sha256": text_sha256(system),
                "user_sha256": text_sha256(user),
                "prompt_chars": len(system) + len(user),
            }
        )
    with (out_dir / "requests.jsonl").open("w", encoding="utf-8") as fh:
        for row in plan_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_domain = Counter(u.domain for u in units)
    by_register = Counter(u.register for u in units)
    by_field = Counter(u.subject_field for u in units)
    by_digits = Counter(u.digit_style for u in units)
    by_image = Counter(bool(u.image_slot) for u in units)
    by_table = Counter(
        bool(reg["domains"][u.domain].get("tables_expected", 0)) for u in units
    )
    print(f"run dir     : {out_dir.relative_to(ROOT)}")
    print(f"units       : {len(units)}  (seed {args.seed})")
    print(f"models      : {', '.join(models)}")
    print(f"domains     : {dict(sorted(by_domain.items()))}")
    print(f"registers   : {dict(sorted(by_register.items()))}")
    print(f"fields      : {dict(sorted(by_field.items()))}")
    print(f"digit style : {dict(sorted(by_digits.items()))}")
    print(f"image slots : {dict(sorted(by_image.items()))}  (D37)")
    print(f"tbl expected: {dict(sorted(by_table.items()))}  (tables_expected)")

    if args.dry_run:
        sysmsg, usermsg = build_prompt(units[0], reg, prm)
        print("\n--- sample prompt (system) ---")
        print(sysmsg.strip())
        print("\n--- sample prompt (user) ---")
        print(usermsg.strip())
        print(
            f"\nplan written: {(out_dir / 'requests.jsonl').relative_to(ROOT)} "
            f"({len(plan_rows)} rows)"
        )
        return 0

    _read_env()
    raw = os.environ.get(gen["provider"]["key_env"])
    pool_keys = parse_keys(raw)
    if not pool_keys:
        raise SystemExit(
            f"{gen['provider']['key_env']} is empty: export it or use --dry-run"
        )
    print(
        f"keys        : {len(pool_keys)} "
        f"(fixed capacity {len(pool_keys) * gen['provider']['requests_per_minute_per_key']:.0f} req/min)"
    )

    result = asyncio.run(
        dispatch(
            units=units,
            models=models,
            gen=gen,
            reg=reg,
            prm=prm,
            out_dir=out_dir,
            concurrency=args.concurrency,
            max_attempts=args.max_attempts,
            resume=args.resume,
            request_timeout=args.request_timeout,
            rpm_override=args.rpm,
        )
    )

    # re-read accepted records from the shards to apply per-register soft thresholds
    accepted: list[dict[str, object]] = []
    for shard in sorted((out_dir / "shards").glob("shard-*.jsonl")):
        for line in shard.read_text(encoding="utf-8").splitlines():
            if line.strip():
                accepted.append(json.loads(line))
    accepted[:] = [recompute_metrics(r, reg, prm) for r in accepted]
    apply_register_thresholds(accepted, reg, prm)

    words = [int(r["words"]) for r in accepted]
    flag_counts = Counter(
        f for r in accepted for f in (r.get("gate") or {}).get("soft_flags", [])
    )
    manifest = {
        "run_id": out_dir.name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config_sha256": _config_hashes(),
        "seed": args.seed,
        "units_planned": len(units),
        "workers": args.workers,
        "worker_index": args.worker_index,
        "model_candidates": models,
        "provider_base_url": gen["provider"]["base_url"],
        "generation_params": gen["generation"],
        "keys_used": [k["id"] for k in result["keys"]["keys"]],  # fingerprints only
        "accepted": len(accepted),
        "rejected": int(result["rejected"]),
        "words_total": sum(words),
        "words_median": sorted(words)[len(words) // 2] if words else 0,
        "soft_flags": dict(flag_counts),
        "rounds": result["rounds"],
        "ledger_counts": result["ledger"],
        "artefacts": {
            "plan": "requests.jsonl",
            "ledger": "ledger.jsonl",
            "accepted": "shards/shard-*.jsonl",
            "rejects": "rejects.jsonl",
        },
        "provenance": "synthetic_native",
        "note": "Rejects are retained deliberately. A high reject rate is a finding about "
        "the generator, not a batch to be silently retried away.",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for rec in accepted:
        with (out_dir / "shards" / "accepted-with-flags.jsonl").open(
            "a", encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\naccepted    : {len(accepted)}")
    print(f"rejected    : {result['rejected']}")
    print(f"words       : total {sum(words)}, median {manifest['words_median']}")
    print(f"soft flags  : {dict(flag_counts) or '{}'}")
    print(f"ledger      : {result['ledger']}")
    print(f"manifest    : {(out_dir / 'manifest.json').relative_to(ROOT)}")
    for rnd in result["rounds"]:
        print(
            f"  round {rnd['model']}: sent {rnd['sent']} ok {rnd['ok']} "
            f"rejected {rnd['rejected']} failed {rnd['failed']} retries {rnd['retries']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
