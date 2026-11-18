"""Crash-safe request ledger.

A 200,000-request batch that loses its progress on interruption is a batch you
never finish: you either restart from zero (paying the rate limit again) or you
guess which requests already succeeded. So the ledger is append-only JSONL and
every record is fsync'd before the next request is dispatched.

Properties relied upon by the dispatcher:

- **Idempotency.** A request id is a hash of (model, prompt, parameters). The
  same prompt sent twice produces the same id, so a resumed run never re-sends
  a completed request and never double-counts it in the cost accounting.
- **Crash safety.** Each line is a complete JSON object written with a single
  ``write`` + ``fsync``. A torn last line is discarded on replay rather than
  corrupting the state.
- **History, not mutation.** Status transitions are appended
  (``pending → inflight → ok``); the current status is the last record for an
  id. Nothing is rewritten in place, so an audit can reconstruct what happened
  and when.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Status(StrEnum):
    PENDING = "pending"
    INFLIGHT = "inflight"
    OK = "ok"
    FAILED = "failed"
    REJECTED = "rejected"  # produced text that failed a content gate


TERMINAL = {Status.OK, Status.FAILED, Status.REJECTED}


def request_id(*, model: str, prompt: str, params: dict[str, Any], variant: str = "") -> str:
    """Stable id for a (model, prompt, params) triple.

    Deliberately excludes the API key, the timestamp and the shard: those are
    transport details, and including them would defeat resumption.
    """
    payload = json.dumps(
        {"model": model, "prompt": prompt, "params": params, "variant": variant},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Record:
    request_id: str
    status: Status
    ts: float
    error: str | None = None
    attempts: int = 0
    output_sha256: str | None = None
    usage: dict[str, int] | None = None
    extra: dict[str, Any] | None = None


class Ledger:
    """Append-only JSONL ledger with replay-on-open."""

    def __init__(self, path: Path | str, *, fsync: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fsync = fsync
        self._state: dict[str, Record] = {}
        self._replay()

    # -- lifecycle ---------------------------------------------------------
    def _replay(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    rec = Record(
                        request_id=obj["request_id"],
                        status=Status(obj["status"]),
                        ts=float(obj["ts"]),
                        error=obj.get("error"),
                        attempts=int(obj.get("attempts", 0)),
                        output_sha256=obj.get("output_sha256"),
                        usage=obj.get("usage"),
                        extra=obj.get("extra"),
                    )
                except (json.JSONDecodeError, KeyError, ValueError):
                    # A torn final line is expected after a hard kill.
                    continue
                self._state[rec.request_id] = rec

    def _append(self, rec: Record) -> None:
        payload = {
            "request_id": rec.request_id,
            "status": str(rec.status),
            "ts": rec.ts,
            "attempts": rec.attempts,
        }
        if rec.error is not None:
            payload["error"] = rec.error
        if rec.output_sha256 is not None:
            payload["output_sha256"] = rec.output_sha256
        if rec.usage is not None:
            payload["usage"] = rec.usage
        if rec.extra:
            payload["extra"] = rec.extra
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            fh.flush()
            if self._fsync:
                os.fsync(fh.fileno())
        self._state[rec.request_id] = rec

    # -- queries -----------------------------------------------------------
    def status(self, rid: str) -> Status | None:
        rec = self._state.get(rid)
        return rec.status if rec else None

    def is_terminal(self, rid: str) -> bool:
        return self.status(rid) in TERMINAL

    def get(self, rid: str) -> Record | None:
        return self._state.get(rid)

    def outstanding(self, ids: list[str]) -> list[str]:
        """Ids with no terminal record — the resume set."""
        return [i for i in ids if not self.is_terminal(i)]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rec in self._state.values():
            out[str(rec.status)] = out.get(str(rec.status), 0) + 1
        return out

    def total_tokens(self) -> int:
        return sum(
            int((rec.usage or {}).get("total_tokens", 0)) for rec in self._state.values()
        )

    # -- transitions -------------------------------------------------------
    def mark_inflight(self, rid: str, *, attempts: int) -> None:
        self._append(Record(rid, Status.INFLIGHT, time.time(), attempts=attempts))

    def mark_ok(
        self,
        rid: str,
        *,
        attempts: int,
        output_sha256: str,
        usage: dict[str, int] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._append(
            Record(
                rid,
                Status.OK,
                time.time(),
                attempts=attempts,
                output_sha256=output_sha256,
                usage=usage,
                extra=extra,
            )
        )

    def mark_failed(self, rid: str, *, attempts: int, error: str) -> None:
        self._append(
            Record(rid, Status.FAILED, time.time(), error=error[:500], attempts=attempts)
        )

    def mark_rejected(
        self, rid: str, *, attempts: int, reason: str, extra: dict[str, Any] | None = None
    ) -> None:
        self._append(
            Record(
                rid,
                Status.REJECTED,
                time.time(),
                error=reason[:500],
                attempts=attempts,
                extra=extra,
            )
        )