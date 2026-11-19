"""Resumable, rate-limited batch dispatcher.

The transport is injected, so this module never opens a socket: tests run the
whole state machine offline with a fake transport, and the real NVIDIA client is
one small adapter at the edge (see the Gate-3 execution plan). That boundary is
deliberate — the part that is easy to get wrong (resumption, retries, key
rotation, accounting) must be testable without a network.

Four invariants the dispatcher enforces:

1. **No request is sent twice** — the ledger is consulted before dispatch, and the
   request id is a hash of the payload, not of the attempt.
2. **Rhythm before latency** — every send acquires a token from a specific key's
   bucket, so the batch keeps the provider's per-key rhythm instead of racing
   into 429s.
3. **A dead key is not a dead batch** — auth failures quarantine that key and the
   same request is retried on another key.
4. **Every outcome is recorded** — success, retry exhaustion and content
   rejection alike; a rejected generation is data about the generator.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from persdocbench.content.gates import (
    GateReport,
    evaluate,
    normalize_for_storage,
    text_sha256,
)
from persdocbench.content.keys import Key, KeyPool
from persdocbench.content.ledger import Ledger, request_id
from persdocbench.content.ratelimit import Budget


class Retryable(Exception):
    """Transport failure worth retrying (429, timeout, 5xx, truncated body)."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited or retry_after is not None


class Fatal(Exception):
    """Transport failure not worth retrying with this key (400, policy refusal)."""


class AuthError(Fatal):
    """Key rejected (401/403). Retry the request on another key."""


class Transport(Protocol):
    def __call__(self, spec: Request, key: Key) -> Awaitable[Response]: ...


@dataclass(frozen=True)
class Request:
    prompt: str
    model: str
    params: dict[str, object] = field(default_factory=dict)
    variant: str = ""
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return request_id(
            model=self.model, prompt=self.prompt, params=self.params, variant=self.variant
        )


@dataclass(frozen=True)
class Response:
    text: str
    usage: dict[str, int] | None = None


@dataclass
class Stats:
    sent: int = 0
    skipped_done: int = 0
    ok: int = 0
    rejected: int = 0
    failed: int = 0
    retries: int = 0
    quarantined_keys: int = 0
    tokens: int = 0
    gate_reports: dict[str, GateReport] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "sent": self.sent,
            "skipped_done": self.skipped_done,
            "ok": self.ok,
            "rejected": self.rejected,
            "failed": self.failed,
            "retries": self.retries,
            "quarantined_keys": self.quarantined_keys,
            "tokens": self.tokens,
        }


class Dispatcher:
    def __init__(
        self,
        transport: Transport,
        ledger: Ledger,
        pool: KeyPool,
        *,
        concurrency: int = 8,
        max_attempts: int = 5,
        backoff_base: float = 2.0,
        backoff_cap: float = 120.0,
        budget: Budget | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        banned: Sequence[str] = (),
        lexicon: Sequence[str] = (),
        min_term_density: float = 0.0,
        min_numeric_density: float = 0.0,
        sentence_words_range: tuple[float, float] | None = None,
        on_ok: Callable[[Request, Response, GateReport], None] | None = None,
        on_reject: Callable[[Request, Response, GateReport], None] | None = None,
    ) -> None:
        self.transport = transport
        self.ledger = ledger
        self.pool = pool
        self.concurrency = max(1, concurrency)
        self.max_attempts = max(1, max_attempts)
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.budget = budget
        self._sleep = sleeper
        self._rng = rng or random.Random(0)
        self.banned = tuple(banned)
        self.lexicon = tuple(lexicon)
        self.min_term_density = min_term_density
        self.min_numeric_density = min_numeric_density
        self.sentence_words_range = sentence_words_range
        self.on_ok = on_ok
        self.on_reject = on_reject
        self.stats = Stats()

    # -- single request ----------------------------------------------------
    async def _one(self, req: Request) -> None:
        rid = req.id
        if self.ledger.is_terminal(rid):
            self.stats.skipped_done += 1
            return
        if self.budget is not None and not self.budget.can_send():
            self.stats.failed += 1
            self.ledger.mark_failed(rid, attempts=0, error="budget_exhausted")
            return

        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            try:
                key = await self.pool.acquire()
            except RuntimeError as exc:  # every key quarantined
                self.stats.failed += 1
                self.ledger.mark_failed(rid, attempts=attempt, error=str(exc))
                return
            self.stats.sent += 1
            self.ledger.mark_inflight(rid, attempts=attempt)

            try:
                resp = await self.transport(req, key)
            except AuthError as exc:
                if not key.disabled:
                    self.pool.quarantine(key, reason=f"auth: {exc}")
                    self.stats.quarantined_keys += 1
                # Not counted as an attempt against the request: the payload was
                # never evaluated by the provider.
                attempt -= 1
                if not self.pool.active:
                    self.stats.failed += 1
                    self.ledger.mark_failed(
                        rid, attempts=attempt, error="all_keys_quarantined"
                    )
                    return
                continue
            except Retryable as exc:
                self.stats.retries += 1
                if exc.rate_limited:
                    self.pool.report_rate_limited(
                        key, retry_after=exc.retry_after or 60.0
                    )
                else:
                    self.pool.report_failure(key, str(exc))
                if attempt >= self.max_attempts:
                    self.stats.failed += 1
                    self.ledger.mark_failed(
                        rid, attempts=attempt, error=f"retryable: {exc}"
                    )
                    return
                delay = exc.retry_after or min(
                    self.backoff_cap,
                    self.backoff_base**attempt + self._rng.random(),
                )
                await self._sleep(delay)
                continue
            except Fatal as exc:
                self.pool.report_failure(key, str(exc))
                self.stats.failed += 1
                self.ledger.mark_failed(rid, attempts=attempt, error=f"fatal: {exc}")
                return

            if self.budget is not None:
                self.budget.charge(1, int((resp.usage or {}).get("total_tokens", 0)))
            clean = normalize_for_storage(resp.text)
            report = evaluate(
                clean,
                banned=self.banned,
                lexicon=self.lexicon,
                min_term_density=self.min_term_density,
                min_numeric_density=self.min_numeric_density,
                sentence_words_range=self.sentence_words_range,
            )
            self.stats.tokens += int((resp.usage or {}).get("total_tokens", 0))
            if report.verdict == "reject":
                self.stats.rejected += 1
                self.ledger.mark_rejected(
                    rid,
                    attempts=attempt,
                    reason=",".join(report.reasons),
                    extra={"gate": report.as_dict(), "meta": req.meta, "key": key.id},
                )
                if self.on_reject is not None:
                    self.on_reject(req, Response(clean, resp.usage), report)
                return
            self.stats.ok += 1
            self.stats.gate_reports[rid] = report
            self.ledger.mark_ok(
                rid,
                attempts=attempt,
                output_sha256=text_sha256(clean),
                usage=resp.usage,
                extra={"gate": report.as_dict(), "meta": req.meta, "key": key.id},
            )
            if self.on_ok is not None:
                self.on_ok(req, Response(clean, resp.usage), report)
            return

    # -- batch -------------------------------------------------------------
    async def run(self, requests: Iterable[Request]) -> Stats:
        specs = list(requests)
        pending = [r for r in specs if not self.ledger.is_terminal(r.id)]
        self.stats.skipped_done = len(specs) - len(pending)
        sem = asyncio.Semaphore(self.concurrency)

        async def guarded(req: Request) -> None:
            async with sem:
                await self._one(req)

        await asyncio.gather(*(guarded(r) for r in pending))
        return self.stats
