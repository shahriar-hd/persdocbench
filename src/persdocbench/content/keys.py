"""Key pool: several provider keys, each with its own rhythm.

Two facts force this design:

1. The rate limit is **per key**, not global (NVIDIA's hosted endpoints are
   documented by the operator at roughly 60 requests/minute per key). Two keys
   are two rhythms; one shared bucket would waste half the throughput.
2. A key can die mid-batch (quota exhausted, revoked, 401, provider-side
   suspension). A batch that stops because key #1 expired is a batch you restart
   by hand; a pool that quarantines the key and keeps going is a batch you leave
   running overnight.

Selection policy: pick the enabled, non-cooling key whose bucket will be ready
soonest. That keeps keys in lockstep without hammering one into 429s.

**Secrets never leave this module.** Keys are addressed by a short hash
(``sha256(secret)[:8]``) so the ledger, the run manifest and the logs can name
*which* key did what without ever containing the key. ``__repr__`` and
``snapshot()`` are audited by tests for exactly this.

Operational caveat that belongs in the record, not in a footnote: aggregating
many free-tier keys to multiply throughput may conflict with the provider's
terms of service and with per-account quotas. That is an operator decision, and
it is recorded as such in the Gate-3 execution plan — the code simply makes the
choice explicit and auditable instead of hiding it in a shell loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from persdocbench.content.ratelimit import TokenBucket

_clock_default: Callable[[], float] = time.monotonic


def key_fingerprint(secret: str) -> str:
    """Stable, non-reversible id for a key. Used everywhere a key must be named."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def parse_keys(raw: str | None) -> list[str]:
    """Parse a comma-separated key list.

    Accepts the ``NVIDIA_API_KEY=nv_key1,nv_key2`` form the operator actually
    uses, tolerates whitespace and trailing commas, preserves order and removes
    exact duplicates. Raises on an empty result so a misconfigured environment
    fails before the first request rather than after the first 401.
    """
    if not raw:
        raise ValueError("no API key(s) configured")
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split(","):
        k = part.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    if not out:
        raise ValueError("no usable API key(s) in the provided value")
    return out


@dataclass
class Key:
    """One credential plus its own rhythm and health state."""

    secret: str = field(repr=False)
    bucket: TokenBucket
    disabled: bool = False
    cooldown_until: float = 0.0
    requests: int = 0
    failures: int = 0
    last_error: str | None = None

    @property
    def id(self) -> str:
        return key_fingerprint(self.secret)

    def __repr__(self) -> str:  # must never expose the secret
        return (
            f"Key(id={self.id!r}, disabled={self.disabled}, "
            f"requests={self.requests}, failures={self.failures})"
        )


class KeyPool:
    def __init__(
        self,
        secrets: list[str],
        *,
        rate_per_minute: float = 60.0,
        burst: int | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], object] | None = None,
        stagger_seconds: float = 0.0,
    ) -> None:
        if not secrets:
            raise ValueError("KeyPool requires at least one key")
        self._clock = clock or _clock_default
        self._sleep = sleeper or asyncio.sleep
        self.rate_per_minute = rate_per_minute
        self.stagger = stagger_seconds
        now = self._clock()
        self.keys: list[Key] = []
        for i, secret in enumerate(secrets):
            bucket = TokenBucket(
                rate_per_minute,
                burst=burst,
                clock=self._clock,
                sleeper=self._sleep,  # type: ignore[arg-type]
            )
            key = Key(secret=secret, bucket=bucket)
            if stagger_seconds:
                key.cooldown_until = now + i * stagger_seconds
            self.keys.append(key)
        self._rr = 0
        self._lock = asyncio.Lock()
        self.slept_seconds = 0.0

    # -- construction ------------------------------------------------------
    @classmethod
    def from_env(
        cls, var: str = "NVIDIA_API_KEY", **kwargs: object
    ) -> "KeyPool":
        return cls(parse_keys(os.environ.get(var)), **kwargs)  # type: ignore[arg-type]

    # -- capacity ----------------------------------------------------------
    @property
    def active(self) -> list[Key]:
        return [k for k in self.keys if not k.disabled]

    @property
    def capacity_per_minute(self) -> float:
        return self.rate_per_minute * len(self.active)

    def eta_seconds(self, remaining: int) -> float:
        cap = self.capacity_per_minute
        return float("inf") if cap <= 0 else remaining / (cap / 60.0)

    # -- selection ---------------------------------------------------------
    def _eligible(self, now: float) -> list[Key]:
        return [k for k in self.active if k.cooldown_until <= now]

    def _pick(self, now: float) -> Key | None:
        elig = self._eligible(now)
        if not elig:
            return None
        # Soonest-ready bucket wins; round-robin breaks exact ties so keys age evenly.
        elig.sort(key=lambda k: (k.bucket.wait_time(), self.keys.index(k)))
        order = elig[self._rr % len(elig):] + elig[: self._rr % len(elig)]
        self._rr += 1
        best = min(order, key=lambda k: k.bucket.wait_time())
        return best

    async def acquire(self) -> Key:
        """Block until one key has a free token, then consume it.

        Waits are computed, not polled. If every key is cooling down or
        disabled the call sleeps until the earliest cooldown expires.
        """
        while True:
            async with self._lock:
                now = self._clock()
                key = self._pick(now)
                if key is not None:
                    delay = key.bucket.wait_time()
                    if delay <= 0:
                        key.bucket.try_acquire()
                        key.requests += 1
                        return key
            waits = [k.bucket.wait_time() for k in self._eligible(now)]
            if not waits:
                future = [k.cooldown_until for k in self.active]
                if not future:
                    raise RuntimeError("all API keys are disabled")
                delay = max(0.0, min(future) - now)
                if delay <= 0:
                    delay = 0.5
            else:
                delay = min(waits)
            delay = max(delay, 0.01)
            await self._sleep(delay)
            self.slept_seconds += delay

    # -- health ------------------------------------------------------------
    def report_rate_limited(self, key: Key, retry_after: float = 60.0) -> None:
        key.cooldown_until = self._clock() + retry_after
        key.failures += 1
        key.last_error = f"rate_limited(retry_after={retry_after:g})"

    def report_failure(self, key: Key, error: str) -> None:
        key.failures += 1
        key.last_error = error[:200]

    def quarantine(self, key: Key, *, reason: str) -> None:
        """Disable a key permanently for this run (401/403/revoked)."""
        key.disabled = True
        key.failures += 1
        key.last_error = reason[:200]

    # -- audit -------------------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        """Manifest-safe view: ids and counters, never secrets."""
        return {
            "keys": [
                {
                    "id": k.id,
                    "disabled": k.disabled,
                    "requests": k.requests,
                    "failures": k.failures,
                    "last_error": k.last_error,
                }
                for k in self.keys
            ],
            "rate_per_minute_per_key": self.rate_per_minute,
            "capacity_per_minute": self.capacity_per_minute,
        }