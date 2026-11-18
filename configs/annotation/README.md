# `configs/annotation`

Versioned configuration profiles for the **annotation** stage.

Conventions:

- One file per named profile (`<profile>.yaml`), never one mutable `default.yaml`.
- Every profile is hashed into the run manifest, so a result always names its config.
- Profiles are additive: a stage config references the profile ids it consumes.
- No secrets in this directory. Credentials come from the environment.

## Profiles

| Profile | Status | Notes |
|---|---|---|
| `ontology_v1.yaml` | **draft** (Gate 2) | label ontology: 18 layout classes (incl. `marginal_note`, `stamp`, `signature`), attributes, text fields, table model, reading order, VQA item schema |

Human-readable twin: `docs/dataset/label_ontology_v1.md`.
The ontology is a closed set in v1 — adding a class is a versioned change, not an edit.
