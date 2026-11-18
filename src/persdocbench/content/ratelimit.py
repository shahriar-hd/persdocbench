"""Token-bucket rate limiting for provider APIs.

Provider free tiers are limited per minute (NVIDIA's hosted endpoints are
documented by the operator at roughly 60 requests/minute). The limit, not the
host, is what determines how long a batch takes: 6,000 requests at 60/min is
100 minutes of wall clock no matter how fast the machine is, and no faster if
you slow it down. Hence a rhythm limiter, not a semaphore.

Design notes:
- The bucket is the *rate*; concurrency is a separate knob (how many requests
  are in flight). Both are needed: 60 requests/minute with 60 s latency
  requires ~60 in flight to stay on the rhythm.
- The clock is injectable so tests are deterministic and instant.
- Nothing here sleeps in a busy loop; waiting is a single computed sleep.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class TokenBucket:
    """Classic token bucket: ``rate`` tokens per second, capacity ``burst``."""

    def __init__(
        self,
        rate_per_minute: float,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], "asyncio.Future[None] | object"] | None = None,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self.rate = rate_per_minute / 60.0
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_minute)))
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()
        self._sleep = sleeper or asyncio.sleep
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking acquire. Returns False when the bucket is empty."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def wait_time(self, tokens: float = 1.0) -> float:
        """Seconds until ``tokens`` will be available. 0.0 when available now."""
        self._refill()
        deficit = tokens - self._tokens
        return 0.0 if deficit <= 0 else deficit / self.rate

    async def acquire(self, tokens: float = 1.0) -> float:
        """Wait until ``tokens`` are available, then consume them.

        Returns the number of seconds actually slept, which the caller records
        in the run manifest so throttling is auditable rather than invisible.
        """
        slept = 0.0
        while True:
            async with self._lock:
                delay = self.wait_time(tokens)
                if delay <= 0:
                    self._tokens -= tokens
                    return slept
            await self._sleep(delay)
            slept += delay


@dataclass
class Budget:
    """Declared limits for a batch, checked before any request is sent.

    Sending a batch that cannot finish inside the operator's stated quota is a
    planning error that should fail fast, not a surprise 40 hours in.
    """

    requests_per_minute: float
    max_requests: int | None = None
    max_total_tokens: int | None = None
    _used_requests: int = field(default=0, init=False)
    _used_tokens: int = field(default=0, init=False)

    def can_send(self, n: int = 1, tokens: int = 0) -> bool:
        if self.max_requests is not None and self._used_requests + n > self.max_requests:
            return False
        if (
            self.max_total_tokens is not None
            and self._used_tokens + tokens > self.max_total_tokens
        ):
            return False
        return True

    def charge(self, n: int = 1, tokens: int = 0) -> None:
        self._used_requests += n
        self._used_tokens += tokens

    @property
    def used_requests(self) -> int:
        return self._used_requests

    @property
    def used_tokens(self) -> int:
        return self._used_tokens

    def eta_seconds(self, remaining: int) -> float:
        """Wall-clock estimate for ``remaining`` requests at this rate."""
        return remaining / (self.requests_per_minute / 60.0)