# `configs/benchmark`

Versioned configuration profiles for the **benchmark** stage.

Conventions:

- One file per named profile (`<profile>.yaml`), never one mutable `default.yaml`.
- Every profile is hashed into the run manifest, so a result always names its config.
- Profiles are additive: a stage config references the profile ids it consumes.
- No secrets in this directory. Credentials come from the environment.

## Profiles

| Profile | Status | Notes |
|---|---|---|
| `benchmark_v1.yaml` | **draft** (Gate 2) | composition, conditions, splits, VQA budget for PersDocBench v1; machine-readable twin of `docs/dataset/benchmark_spec_v1.md` |

Validate before use — the profile makes many mutually dependent count claims:

```bash
python scripts/validate_benchmark_config.py --check   # arithmetic/consistency only
python scripts/validate_benchmark_config.py           # also regenerates docs/dataset/count_matrix_v1.csv
```

`tests/contract/test_benchmark_config.py` enforces the same invariants in CI.
