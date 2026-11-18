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
| `register_v1.yaml` | **draft** (Gate 3 input) | `subject_field` axis (7 fields), `register` axis (4 tiers), banned lay patterns (hard gate), acceptance checks R1–R6, per-domain register contracts, calibration plan |

Human-readable twin: `docs/dataset/content_register_spec_v1.md`.
Cross-checked against the benchmark profile by `scripts/validate_benchmark_config.py`
(domain set must match, all share axes must sum to 1.0, `R2_banned_patterns.max` must be 0).
