#!/usr/bin/env python3
"""contentd — run the content generator as a background service.

Why this exists
---------------
A 1,500-unit batch at 320–430 words per unit is ~25 minutes of wall clock at one key's
documented rate, and one model call can take 40 s. Running it in a terminal means the
batch dies with the terminal, and a single read timeout (which we observed on a 120 s
read) kills the whole set. So the generator is supervised:

- one **supervisor** process, N **worker** processes, each worker a full
  ``generate_content.py`` run over its own deterministic slice of the plan;
- a worker that exits non-zero is restarted, up to ``--max-restarts`` times per worker;
- state lives in files under the run directory, so ``status`` works without touching
  the running processes, and a restart resumes from the append-only ledger;
- SIGTERM/SIGINT stop children in order and write a final status, so nothing is left
  half-written (the ledger is append-only and the writers fsync every record).

What it does not do
-------------------
It does not make the batch faster than the provider allows: each worker holds its own
``KeyPool`` and the keys are shared, so the *total* rate is still the provider's limit.
Workers exist to overlap latency (40 s of generation per request), not to multiply the
quota. Concurrency above the per-key rate only produces 429s.

Usage
-----
    python scripts/contentd.py start --units 1500 --workers 2
    python scripts/contentd.py status
    python scripts/contentd.py stop
    python scripts/contentd.py logs --tail 40
    python scripts/contentd.py start --units 40 --workers 2 --foreground   # smoke test
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "interim" / "gen" / "service"
PIDFILE = ROOT / "data" / "interim" / "gen" / "contentd.pid"
STATUS = ROOT / "data" / "interim" / "gen" / "contentd.status.json"
STOPFILE = ROOT / "data" / "interim" / "gen" / "contentd.stop"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Worker:
    index: int
    pid: int | None = None
    restarts: int = 0
    state: str = "pending"  # pending | running | done | failed | stopped
    exit_code: int | None = None
    accepted: int = 0
    rejected: int = 0
    log: str = ""


@dataclass
class Service:
    run_dir: str
    units: int
    workers: int
    models: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    state: str = "starting"  # starting | running | done | failed | stopped
    workers_state: list[Worker] = field(default_factory=list)
    note: str = ""

    def write(self) -> None:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(STATUS)


def _counts(run_dir: Path) -> tuple[int, int]:
    """Accepted and rejected counts, read from the run's own artefacts."""
    acc = rej = 0
    for shard in sorted((run_dir / "shards").glob("shard-*.jsonl")):
        with shard.open(encoding="utf-8") as fh:
            acc += sum(1 for line in fh if line.strip())
    rejf = run_dir / "rejects.jsonl"
    if rejf.is_file():
        with rejf.open(encoding="utf-8") as fh:
            rej += sum(1 for line in fh if line.strip())
    return acc, rej


def _worker_cmd(args: argparse.Namespace, index: int, run_dir: Path) -> list[str]:
    cmd = [
        sys.executable if not args.python else args.python,
        str(ROOT / "scripts" / "generate_content.py"),
        "--count",
        str(args.units),
        "--workers",
        str(args.workers),
        "--worker-index",
        str(index),
        "--concurrency",
        str(args.concurrency),
        "--max-attempts",
        str(args.max_attempts),
        "--request-timeout",
        str(args.request_timeout),
        "--seed",
        str(args.seed),
        "--out",
        str(run_dir),
    ]
    if args.rpm:
        cmd += ["--rpm", str(args.rpm)]
    if args.resume:
        cmd.append("--resume")
    for d in args.domain or []:
        cmd += ["--domain", d]
    for f in args.subject_field or []:
        cmd += ["--subject-field", f]
    if args.register:
        cmd += ["--register", args.register]
    return cmd


def _spawn(cmd: list[str], log_path: Path) -> subprocess.Popen[bytes]:
    """Start a worker fully detached from this terminal's signal group.

    ``start_new_session`` matters: without it a Ctrl-C in the launching shell reaches
    every worker and the batch dies even though the supervisor was asked to keep going.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        env=env,
        start_new_session=True,
    )


def supervise(args: argparse.Namespace) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "data" / "interim" / "gen" / f"service-{stamp}"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    STOPFILE.unlink(missing_ok=True)

    svc = Service(
        run_dir=str(run_dir.relative_to(ROOT)), units=args.units, workers=args.workers
    )
    svc.workers_state = [
        Worker(index=i, log=str((run_dir / "logs" / f"w{i}.log").relative_to(ROOT)))
        for i in range(args.workers)
    ]
    svc.write()
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")

    stopping = {"flag": False}

    def _stop(signum: int, _frame: object) -> None:
        stopping["flag"] = True
        svc.note = f"received signal {signum}; stopping workers"
        svc.write()
        for w in svc.workers_state:
            if w.pid:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(w.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    svc.state = "running"
    svc.write()
    procs: dict[int, subprocess.Popen[bytes]] = {}

    for w in svc.workers_state:
        cmd = _worker_cmd(args, w.index, run_dir / f"w{w.index}")
        procs[w.index] = _spawn(cmd, run_dir / "logs" / f"w{w.index}.log")
        w.pid = procs[w.index].pid
        w.state = "running"

    deadline = time.time() + args.max_hours * 3600 if args.max_hours else None
    try:
        while procs:
            time.sleep(args.poll_seconds)
            for w in svc.workers_state:
                proc = procs.get(w.index)
                if proc is None:
                    continue
                rc = proc.poll()
                if rc is None:
                    continue
                w.exit_code = rc
                if rc == 0:
                    w.state = "done"
                    procs.pop(w.index, None)
                    continue
                # non-zero: restart unless we are stopping or out of restarts
                if (
                    stopping["flag"]
                    or STOPFILE.exists()
                    or w.restarts >= args.max_restarts
                ):
                    w.state = "stopped" if stopping["flag"] else "failed"
                    procs.pop(w.index, None)
                    continue
                if deadline and time.time() > deadline:
                    w.state = "failed"
                    svc.note = f"max-hours reached; worker {w.index} not restarted"
                    procs.pop(w.index, None)
                    continue
                w.restarts += 1
                w.state = "running"
                cmd = _worker_cmd(args, w.index, run_dir / f"w{w.index}")
                proc = _spawn(cmd, run_dir / "logs" / f"w{w.index}.log")
                procs[w.index] = proc
                w.pid = proc.pid
            if STOPFILE.exists() and not stopping["flag"]:
                _stop(signal.SIGTERM, None)

            for w in svc.workers_state:
                w.accepted, w.rejected = _counts(run_dir / f"w{w.index}")
            svc.updated_at = _now()
            svc.state = "stopping" if stopping["flag"] else "running"
            svc.write()

            if deadline and time.time() > deadline and procs:
                svc.note = "max-hours reached; terminating"
                _stop(signal.SIGTERM, None)
    finally:
        for w in svc.workers_state:
            if w.state == "running":
                w.state = "stopped"
        svc.state = (
            "done" if all(w.state == "done" for w in svc.workers_state) else "stopped"
        )
        svc.updated_at = _now()
        if svc.state == "done":
            svc.note = svc.note or "all workers finished"
        svc.write()
        PIDFILE.unlink(missing_ok=True)
        STOPFILE.unlink(missing_ok=True)
    return 0


# --------------------------------------------------------------------------
# control surface
# --------------------------------------------------------------------------
def _pid() -> int | None:
    if not PIDFILE.is_file():
        return None
    try:
        pid = int(PIDFILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def _status() -> int:
    if not STATUS.is_file():
        print("no service has been started (no status file)")
        return 1
    data = json.loads(STATUS.read_text(encoding="utf-8"))
    pid = _pid()
    print(
        f"state      : {data['state']}" + ("" if pid else "   (supervisor not running)")
    )
    print(f"run dir    : {data['run_dir']}")
    print(f"units      : {data['units']} over {data['workers']} worker(s)")
    print(f"started    : {data['started_at']}   updated: {data['updated_at']}")
    acc = sum(w["accepted"] for w in data["workers_state"])
    rej = sum(w["rejected"] for w in data["workers_state"])
    print(f"accepted   : {acc}   rejected: {rej}")
    for w in data["workers_state"]:
        exit_txt = "" if w.get("exit_code") is None else f"  exit {w['exit_code']}"
        print(
            f"  w{w['index']}  {w['state']:<9} pid {w['pid']!s:<8} "
            f"restarts {w['restarts']}  ok {w['accepted']}  rej {w['rejected']}{exit_txt}"
        )
    if data.get("note"):
        print(f"note       : {data['note']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("action", choices=["start", "status", "stop", "logs", "foreground"])
    ap.add_argument("--units", type=int, default=1500)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--request-timeout", type=float, default=180.0)
    ap.add_argument("--max-restarts", type=int, default=3)
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--poll-seconds", type=float, default=15.0)
    ap.add_argument("--rpm", type=float, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--python", default=None)
    ap.add_argument("--domain", action="append")
    ap.add_argument("--subject-field", action="append")
    ap.add_argument("--register", default=None)
    ap.add_argument("--tail", type=int, default=30)
    args = ap.parse_args(argv)

    if args.action == "status":
        return _status()
    if args.action == "stop":
        pid = _pid()
        if pid is None:
            print("not running")
            STOPFILE.parent.mkdir(parents=True, exist_ok=True)
            STOPFILE.write_text(_now(), encoding="utf-8")
            return 0
        STOPFILE.parent.mkdir(parents=True, exist_ok=True)
        STOPFILE.write_text(_now(), encoding="utf-8")
        os.kill(pid, signal.SIGTERM)
        print(f"asked supervisor {pid} to stop")
        return 0
    if args.action == "logs":
        data = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.is_file() else {}
        for w in data.get("workers_state", []):
            print(f"--- {w['log']} ---")
            p = ROOT / w["log"]
            if p.is_file():
                print("\n".join(p.read_text(encoding="utf-8").splitlines()[-args.tail :]))
        return 0

    if _pid() is not None:
        raise SystemExit("already running; use `status` or `stop` first")

    if args.action in ("start", "foreground"):
        if args.action == "start":
            # detach: the service must outlive this shell and this Pi session
            log = ROOT / "data" / "interim" / "gen" / "contentd.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            # the detached copy runs the same command with the action renamed, so it
            # inherits every flag without a second parsing path
            argv2 = [
                ("foreground" if a == "start" else a) for a in (argv or sys.argv[1:])
            ]
            with log.open("ab") as fh:
                subprocess.Popen(
                    [
                        sys.executable if not args.python else args.python,
                        str(Path(__file__).resolve()),
                        *argv2,
                    ],
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    cwd=str(ROOT),
                    start_new_session=True,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            time.sleep(1.5)
            print(f"started detached; log {log.relative_to(ROOT)}")
            return _status()
        return supervise(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
