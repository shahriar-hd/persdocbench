# AGENTS.md — PersDocBench

Project-specific rules for any agent working in this repository. These sit on top of the
global rules in `~/.pi/agent/AGENTS.md`.

## 1. Commit policy — confirmation required, every time

**DO NOT COMMIT BEFORE CONFIRM.** Even when a task says "add this and commit", the
sequence is always:

1. make the change;
2. report it (files touched, what changed, what is verified vs. unverified);
3. **write the proposed commit message**;
4. wait for the user's confirmation;
5. only then `git commit` and `git push` to the branch the user names.

Never `--amend`, `reset --hard`, force-push, or rewrite published history without a
separate confirmation naming that operation.

## 2. What may never be committed

Approved at Gate 0: only **code, schemas, configs, tests, packaging and project docs/**
may be tracked. Everything below is git-ignored and must stay out of git, out of
published artefacts, and out of any remote:

`docs/` · `old/` · `skills/` · `.pi/` · `.claude/` · `data/` · `experiments/` ·
`artifacts/` · environments · secrets · weights · images/PDFs.

If `git status` ever shows one of these as staged, that is a bug — stop and report it.

## 3. Research integrity rules (from the Gate-1 review)

- Anchor papers and resources by **ID/DOI**, never by keyword. 13 of 34 keyword lookups
  in this project misfired (38 %).
- Every number carries **scale + unit + evidence tier** (T1 peer-reviewed artefact
  inspected · T2 preprint with inspectable artefact · T3 unrefereed · T4 secondary).
- Mark unverified metadata `n/v`. Never fill a gap with a plausible estimate.
- Report `strict` **and** normalized Persian scores, always
  (`docs/research/metric_protocol.md`).
- No LLM output enters ground truth without a human gate plus the prompt-echo detector
  (constraint C4).
- The legacy `old/zilber_ocr` numbers are never quotable as Persian benchmark results.

## 4. Gate discipline

The programme runs in approval gates (see `docs/reports/`). Do not start work belonging
to the next gate — data acquisition, generation, rendering, annotation, training,
evaluation runs — before the current gate is approved in writing.

## 5. Environment

- Run Pi inside the sandbox: `scripts/sandbox/pi-sandboxed.sh` (bubblewrap; personal
  directories are absent, `~/Public` read-only).
- Python 3.12 via `uv` (`requires-python = ">=3.11,<3.13"`); the system default 3.14 has
  no usable ML wheels.
- After a `pi` upgrade, re-run `scripts/patches/apply-pi-scholar-idempotency-fix.sh`.

## 6. Response style

Be concise. Show file paths. Separate **reported** from **reproduced** from **proposed**.
State limitations and unknowns instead of smoothing them over.