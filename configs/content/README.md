# `configs/content`

Versioned configuration profiles for the **content** stage.

Conventions:

- One file per named profile (`<profile>.yaml`), never one mutable `default.yaml`.
- Every profile is hashed into the run manifest, so a result always names its config.
- Profiles are additive: a stage config references the profile ids it consumes.
- No secrets in this directory. Credentials come from the environment.

## Profiles

| Profile | Status | Notes |
|---|---|---|
| `generation_v1.yaml` | **draft** (Gate 3 input) | provider/key policy, dispatch rhythm, retry taxonomy, normalisation contract, storage layout, run-manifest fields, hosting limits per batch size |
| `register_v1.yaml` | **draft** (Gate 3 input) | `subject_field` axis (7 fields), `register` axis (4 tiers), banned lay patterns (hard gate), acceptance checks R1–R6, per-domain register contracts, calibration plan |
| `fonts_v1.yaml` | **draft** (Gate 3 input) | Persian font inventory: 43 families in 5 groups (`text_naskh`, `display_titr`, `nastaliq`, `handwriting_like`, `monospace`), licence + sha per family, proprietary families excluded, local-machine inventory report |

Human-readable twins: `docs/dataset/content_register_spec_v1.md` (register),
`docs/dataset/benchmark_spec_v1.md` §3.3 and D30 (fonts).

`fonts_v1.yaml` `counts` are verified against its own `families` lists by the validator, so
a family cannot be added or deleted without the total moving with it. Font **files** are
never redistributed: they are a *rendering* dependency, and rendering text with a font does
not make the page image a derivative of the font software. Handwriting-like families are a
*typographic style*; hand-written pages come from people, under
`docs/dataset/handwriting_capture_protocol_v1.md`, and are a different provenance class.

Scan the host machine (read-only) to see which inventory families are locally usable:

```bash
python scripts/scan_local_fonts.py            # writes artifacts/fonts/local_fonts.json
```
Cross-checked against the benchmark profile by `scripts/validate_benchmark_config.py`
(domain set must match, all share axes must sum to 1.0, `R2_banned_patterns.max` must be 0).

`generation_v1.yaml` is consumed by `src/persdocbench/content/` (key pool, token bucket,
ledger, dispatcher). Its `provider.model` is intentionally `null` — a run must name its
model, and no default is inferred.

Before a generation batch, probe the endpoint and rank what actually answers:

```bash
python scripts/probe_nvidia_models.py --dry-run   # list probe targets
python scripts/probe_nvidia_models.py             # measure, then rank
```

Artefacts land in `artifacts/probes/<UTC-stamp>/` (`models.json`, `results.jsonl`,
`summary.json`, `ranking.md`, `outputs/`). The 2026-11-18 sweep is written up in
`docs/reports/2026-11-18_nvidia_model_probe.md`.
