#!/usr/bin/env python3
"""Probe every reachable NVIDIA endpoint model and rank them.

    python scripts/probe_nvidia_models.py                 # probe the catalogue
    python scripts/probe_nvidia_models.py --models a,b    # probe named models
    python scripts/probe_nvidia_models.py --dry-run       # list models, send nothing

Reads ``NVIDIA_API_KEY`` (comma-separated) from the environment, falling back to
``.env``. Keys are never printed; per-key request counters and fingerprints are
written to the run summary instead.

Artefacts (git-ignored): ``artifacts/probes/<UTC-stamp>/``
    models.json     raw catalogue response
    results.jsonl   one line per (model, probe) measurement
    summary.json    per-model aggregates, rankings and run metadata
    ranking.md      human-readable ranked table
    outputs/        accepted probe texts, one file per model
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from persdocbench.content.dispatcher import AuthError, Fatal, Retryable  # noqa: E402
from persdocbench.content.dotenv import load_dotenv  # noqa: E402
from persdocbench.content.gates import evaluate, find_banned_patterns  # noqa: E402
from persdocbench.content.keys import KeyPool  # noqa: E402
from persdocbench.content.nvidia import (  # noqa: E402
    ModelForbidden,
    ModelUnavailable,
    ModelUnsupported,
    NvidiaClient,
    ParamRejected,
)
from persdocbench.content.probe import (  # noqa: E402
    ProbeRow,
    persian_ratio,
    rank,
    summarise,
    to_markdown,
)

# Two probes, deliberately different: one dense specialist paragraph request
# (the real workload) and one short instruction-following check. Kept tiny so the
# whole sweep costs tens of requests, not thousands.
PROBES: list[dict[str, object]] = [
    {
        "id": "medicine_expert",
        "params": {"temperature": 0.4, "max_tokens": 320, "top_p": 0.9},
        "prompt": (
            "یک بند فارسی تخصصی برای متن اصلی یک مقاله پزشکی بنویس درباره "
            "اتیولوژی و پاتوژنز نارسایی مزمن قلب. متن باید برای متخصص نوشته شود، "
            "شامل اصطلاحات دقیق، اعداد و واحدها باشد، و از توضیح سطح مجله عمومی "
            "پرهیز کند. فقط متن نهایی را بده."
        ),
    },
    {
        "id": "finance_operational",
        "params": {"temperature": 0.4, "max_tokens": 320, "top_p": 0.9},
        "prompt": (
            "یک پاراگراف فارسی رسمی برای بخش شرایط و ضوابط یک قرارداد بیمه بنویس "
            "درباره سقف تعهدات، فرانشیز و مهلت اعلام خسارت. لحن اداری و دقیق باشد، "
            "مبالغ با واحد ریال ذکر شوند. فقط متن نهایی را بده."
        ),
    },
]

EXCLUDE_HINTS = (
    "embed",
    "guard",
    "safety",
    "video-detector",
    "calibration",
    "topic-control",
    "rerank",
    "retriev",
    "starcoder",
)


def load_banned() -> list[str]:
    """Banned lay patterns from the register profile, if PyYAML is available."""
    path = ROOT / "configs" / "content" / "register_v1.yaml"
    try:
        import yaml
    except ModuleNotFoundError:
        return []
    if not path.is_file():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    banned = []
    for key in ("banned_patterns_fa", "banned_patterns_en", "definition_copulas_fa"):
        val = cfg.get(key)
        if isinstance(val, list):
            banned += [str(v) for v in val if str(v).strip()]
    return banned


def load_lexicon() -> list[str]:
    path = ROOT / "configs" / "content" / "register_v1.yaml"
    try:
        import yaml
    except ModuleNotFoundError:
        return []
    if not path.is_file():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms: list[str] = []
    for field_cfg in (cfg.get("subject_fields") or {}).values():
        if isinstance(field_cfg, dict):
            terms += [str(t) for t in (field_cfg.get("lexicon_seed") or [])]
    return terms


async def probe_model(
    client: NvidiaClient,
    pool: KeyPool,
    model: str,
    *,
    max_attempts: int,
    timeout: float,
    forbidden_retries: int = 1,
    banned: list[str],
    lexicon: list[str],
    out_dir: Path | None,
) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    for probe in PROBES:
        attempt = 0
        params = dict(probe["params"])  # type: ignore[arg-type]
        escalations = 0
        dropped: list[str] = []
        forbidden_tries = 0
        while True:
            attempt += 1
            try:
                key = await pool.acquire()
            except RuntimeError as exc:
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="no_keys",
                        error=str(exc),
                    )
                )
                return rows
            started = time.perf_counter()
            try:
                text, usage = await asyncio.to_thread(
                    client.complete_sync,
                    key=key,
                    model=model,
                    prompt=str(probe["prompt"]),
                    params=params,
                    timeout=timeout,
                )
            except AuthError as exc:
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="auth",
                        error=str(exc)[:300],
                    )
                )
                return rows
            except ParamRejected as exc:
                # Model-level parameter policy: drop the offending key and resend.
                if exc.param and exc.param in params and exc.param not in dropped:
                    dropped.append(exc.param)
                    params.pop(exc.param, None)
                    continue
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="param_rejected",
                        error=str(exc)[:300],
                    )
                )
                break
            except ModelForbidden as exc:
                # Observed to be intermittent, not a permanent entitlement answer:
                # nemotron-3-ultra-550b refused twice and then answered on the same
                # key. Retry a bounded number of times before calling it dead.
                if forbidden_tries < forbidden_retries:
                    forbidden_tries += 1
                    await asyncio.sleep(2.0 * forbidden_tries)
                    continue
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="forbidden",
                        error=str(exc)[:300],
                    )
                )
                return rows
            except (ModelUnavailable, ModelUnsupported) as exc:
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class=type(exc).__name__,
                        error=str(exc)[:300],
                    )
                )
                return rows
            except Retryable as exc:
                # A reasoning model can spend the whole budget on hidden tokens and
                # return finish_reason=length. Escalate the budget once, then give up:
                # a 3x retry is a real fix, a loop is not.
                if "truncated" in str(exc) and escalations < 2:
                    escalations += 1
                    params["max_tokens"] = int(params.get("max_tokens", 320)) * 3
                    continue
                if attempt < max_attempts:
                    await asyncio.sleep(exc.retry_after or min(30.0, 2.0**attempt))
                    continue
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="retry_exhausted",
                        error=str(exc)[:300],
                    )
                )
                break
            except Fatal as exc:
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="Fatal",
                        error=str(exc)[:300],
                    )
                )
                break
            except (TimeoutError, ConnectionError) as exc:
                if attempt < max_attempts:
                    await asyncio.sleep(min(30.0, 2.0**attempt))
                    continue
                rows.append(
                    ProbeRow(
                        model=model,
                        probe=str(probe["id"]),
                        ok=False,
                        error_class="timeout"
                        if isinstance(exc, TimeoutError)
                        else "network",
                        error=str(exc)[:300],
                    )
                )
                break

            elapsed = max(time.perf_counter() - started, 1e-6)
            words = len(text.split())
            gate = evaluate(text, banned=banned, lexicon=lexicon)
            hits = find_banned_patterns(text, banned) if banned else []
            row = ProbeRow(
                model=model,
                probe=str(probe["id"]),
                ok=True,
                latency_s=elapsed,
                completion_tokens=usage.completion_tokens,
                tok_per_s=(usage.completion_tokens / elapsed)
                if usage.completion_tokens
                else 0.0,
                chars=len(text),
                words=words,
                persian_ratio=persian_ratio(text),
                gate_verdict=gate.verdict,
                gate_reasons=tuple(gate.reasons),
            )
            rows.append(row)
            if out_dir is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                safe = model.replace("/", "__")
                (out_dir / f"{safe}__{probe['id']}.txt").write_text(
                    text + "\n", encoding="utf-8"
                )
            if hits:
                row.gate_reasons = tuple(
                    list(row.gate_reasons) + [f"banned:{h}" for h in hits]
                )
            break
    return rows


async def run(args: argparse.Namespace) -> int:
    applied = load_dotenv(args.env_file)
    if applied:
        print(f".env: {len(applied)} variable(s) applied from {args.env_file}")

    rate = float(os.environ.get("NVIDIA_REQUESTS_PER_MINUTE", args.rpm))
    pool = KeyPool.from_env("NVIDIA_API_KEY", rate_per_minute=rate, burst=args.burst)
    client = NvidiaClient(
        base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        timeout=args.timeout,
    )
    print(
        f"keys: {len(pool.keys)} (fingerprints {[k.id for k in pool.keys]}), "
        f"{rate:g} req/min/key => {pool.capacity_per_minute:g} req/min total"
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out) / stamp
    out_root.mkdir(parents=True, exist_ok=True)

    # -- catalogue ---------------------------------------------------------
    key = await pool.acquire()
    try:
        catalogue = await asyncio.to_thread(client.list_models, key)
    except Exception as exc:
        print(f"catalogue request failed: {type(exc).__name__}: {exc}")
        return 2
    (out_root / "models.json").write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    ids = [str(m.get("id")) for m in catalogue if m.get("id")]
    print(f"catalogue: {len(ids)} model(s)")

    if args.models:
        wanted = [m.strip() for m in args.models.split(",") if m.strip()]
        targets = [m for m in wanted if m in ids] + [m for m in wanted if m not in ids]
        missing = [m for m in wanted if m not in ids]
        if missing:
            print(f"warning: not in catalogue (will still be probed): {missing}")
    else:
        targets = [m for m in ids if not any(h in m.lower() for h in EXCLUDE_HINTS)]
        if args.limit:
            targets = targets[: args.limit]

    if args.dry_run:
        print("\n".join(targets))
        return 0

    banned = load_banned()
    lexicon = load_lexicon()
    print(
        f"targets: {len(targets)} model(s) x {len(PROBES)} probe(s) = "
        f"{len(targets) * len(PROBES)} request(s); banned patterns: {len(banned)}, "
        f"lexicon terms: {len(lexicon)}"
    )

    sem = asyncio.Semaphore(max(1, args.concurrency))
    banned_hits: dict[tuple[str, str], int] = {}
    results_path = out_root / "results.jsonl"

    async def guarded(model: str) -> list[ProbeRow]:
        async with sem:
            rows = await probe_model(
                client,
                pool,
                model,
                max_attempts=args.max_attempts,
                timeout=args.timeout,
                forbidden_retries=args.forbidden_retries,
                banned=banned,
                lexicon=lexicon,
                out_dir=out_root / "outputs",
            )
            ok = sum(1 for r in rows if r.ok)
            print(f"  {model:<45} {ok}/{len(rows)} ok")
            return rows

    started = time.perf_counter()
    batches = await asyncio.gather(*(guarded(m) for m in targets))
    rows = [r for b in batches for r in b]
    elapsed = time.perf_counter() - started

    with results_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")
            for reason in r.gate_reasons:
                if reason.startswith("banned:"):
                    banned_hits[(r.model, r.probe)] = (
                        banned_hits.get((r.model, r.probe), 0) + 1
                    )

    summaries = summarise(rows, banned_hits)
    ranked, dead = rank(summaries)
    summary = {
        "started_at": stamp,
        "elapsed_s": round(elapsed, 1),
        "catalogue_size": len(ids),
        "models_probed": len(targets),
        "probes_per_model": len(PROBES),
        "requests_attempted": len(targets) * len(PROBES),
        "requests_answered": sum(1 for r in rows if r.ok),
        "rate_per_minute_per_key": rate,
        "keys": pool.snapshot(),
        "ranked": [s.as_dict() for s in ranked],
        "unavailable": [s.as_dict() for s in dead],
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_root / "ranking.md").write_text(
        to_markdown(ranked, dead, probe_count=len(PROBES)), encoding="utf-8"
    )

    print()
    print(to_markdown(ranked, dead, probe_count=len(PROBES)))
    print(
        f"answered {summary['requests_answered']}/{summary['requests_attempted']} in {elapsed:.1f}s"
    )
    print(f"artefacts: {out_root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument(
        "--models",
        default="",
        help="comma-separated model ids; overrides catalogue filtering",
    )
    ap.add_argument(
        "--limit", type=int, default=0, help="probe at most N catalogue models"
    )
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument(
        "--forbidden-retries",
        type=int,
        default=1,
        help="retries after a 403 'authorization failed' (observed to be intermittent)",
    )
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument(
        "--rpm", type=float, default=60.0, help="requests/minute/key (default 60)"
    )
    ap.add_argument("--burst", type=int, default=1, help="immediate tokens per key")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "probes"))
    ap.add_argument("--dry-run", action="store_true", help="list probe targets and exit")
    args = ap.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
