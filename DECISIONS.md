# Decision register — PersDocBench

One place to find every decision the programme is waiting on, what it decides, where its
reasoning lives, and its status. Created because the decisions were spread across six
files under `docs/` (git-ignored) and could not be found from the repository root.

**Status legend**

| Status | Meaning |
|---|---|
| `answered` | the user gave a direction on a date; the reasoning is in the linked file |
| `pending` | proposed and awaiting the user's word. Silence is **not** approval |
| `superseded` | replaced by a later decision; kept visible, never deleted |

---

## 0. Three numbering systems that look alike — read this first

| Namespace | What it numbers | Where |
|---|---|---|
| **T1–T4** | *evidence tiers* of a claim (T1 peer-reviewed artefact inspected · T2 preprint with inspectable artefact · T3 unrefereed · T4 secondary/direct measurement) | `docs/research/benchmark_taxonomy.md` §2 |
| **T1–T7** | *Gate-1 taxonomy decisions* — the questions asked and answered at the literature-review gate | `docs/research/benchmark_taxonomy.md` §9, approval record in `docs/reports/2026-11-17_literature_review.md` §11 |
| **P1–P7** | *metric-protocol decisions* — strict/normalised scoring, CIs, comparison rules | `docs/research/metric_protocol.md`, approved in the same §11 |

So "T3" can mean an evidence tier or a taxonomy decision, depending on the file. When a
number matters, the file is named with it. There is no T8/T9: the Gate-1 list stops at T7.

---

## 1. Benchmark composition and specification (Gate 2)

Reasoning: `docs/dataset/benchmark_spec_v1.md` §12 · summary:
`docs/reports/2026-11-17_gate2_benchmark_spec.md` §5

| ID | Decides | Status |
|---|---|---|
| D1 | Composition: 1,600 content units (1,500 core + 100 parallel control), fixed image budget | `answered` — 6,000 → **5,200** images after D4 |
| D2 | Domain weighting: administrative cluster (letter, form, banking, invoice) ≈ 46 % | `pending` |
| D3 | Script-style mix and the 8 % Nastaliq cap | `answered` — cap kept; **font inventory expanded to ≥ 30 families** (D30) |
| D4 | P2 real-capture size | `answered` 2026-11-18 — **300 pages** (400 images), not 600/1,200 |
| D5 | Two P1 variants (photometric vs geometric) instead of one degraded track | `pending` |
| D6 | Splits 20 / 40 / 40 (dev / public test / sealed test) | `answered in principle` — player asked what "sealed" means; explained as D6/D35, publication model still `pending` |
| D7 | Binding leakage controls: template-disjoint splits, pHash ≤ 6, MinHash ≤ 0.85, dedup by document | `pending` |
| D8 | 3,000 VQA items with the stated category mix | `pending` |
| D9 | Dataset licence and access model | `answered` 2026-11-18 — **CC-BY-NC-4.0 + commercial licence on request** (D33) |
| D10 | Annotation budget and verification sampling | `superseded` by D32 |
| D11 | Arabic parallel control stratum (100 units, outside the headline) | `pending` |
| D12 | Whether handwriting enters v1 | `answered` 2026-11-18 — **in, as a capture mode only** (D34) |

## 2. Content register (Gate 3 input)

Reasoning: `docs/dataset/content_register_spec_v1.md` §7 · profile:
`configs/content/register_v1.yaml`

| ID | Decides | Status |
|---|---|---|
| D13 | `subject_field` as an axis orthogonal to `domain` (7 fields) — how specialist prose enters without an 11th domain | `pending` |
| D14 | `register` axis with a 60 / 25 / 12 / 3 mix; `lay_explainer` banned as a defect | `pending` |
| D15 | A banned-pattern hit is a hard generation failure, not a warning | `pending` |
| D16 | Threshold calibration on 15 samples × 70 cells (1,050), 25th percentile per register tier | `pending` |
| D17 | Label register per content unit and break results down by it | `pending` |
| D18 | Medical/biology stays a subject field, not an 11th domain | `pending` |

## 3. Runtime architecture (ADR-001)

Reasoning: `docs/reports/2026-11-17_adr-001_runtime_architecture.md` §7–8

| ID | Decides | Status |
|---|---|---|
| D19 | Three planes: control, compute, GPU | `pending` |
| D20 | Control plane on Cloudflare Workers + D1 + R2 (free tier constraints respected) | `pending` |
| D21 | Canonical rendering on GitHub Actions with pinned fonts; **no** Cloudflare Browser Rendering | `pending` |
| D22 | Bulk images in a **private** Hugging Face dataset repo, not R2 | `pending` — blocked: HF token expired (`hf auth login`) |
| D23 | GPU plane chosen after inspecting molab / "morino" | `pending` — **needs the URL**; interim fallback Kaggle → Colab → HF ZeroGPU |
| D24 | Release artefacts mirrored to Zenodo/OSF | `pending` |

## 4. Content-generation pipeline

Reasoning: `docs/dataset/content_generation_pipeline_v1.md` §9 · profile:
`configs/content/generation_v1.yaml`

| ID | Decides | Status |
|---|---|---|
| D25 | Pipeline shape: idempotent request ids, append-only ledger, gates that report and never rewrite | `pending` |
| D26 | Key policy: one key at the documented rate for the 6,000-request batch; multi-key aggregation recorded as a compliance fact | `pending` |
| D27 | Hosting: local script for the batch; Oracle Always-Free ARM VM for 200 k; Cloudflare is control plane only | `pending` |
| D28 | Accepted outputs as sharded JSONL + manifest; rejects are a first-class artefact | `pending` |
| D29 | `provider.model` set explicitly per run; no default, no guessing | `pending` |

## 5. Decisions raised by the 2026-11-18 answers (new)

Reasoning: this file's linked documents; config in `configs/content/fonts_v1.yaml`,
`configs/benchmark/benchmark_v1.yaml`.

| ID | Decides | Status |
|---|---|---|
| D30 | Font inventory: ≥ 30 licence-verified Persian families, ≥ 4 handwriting-like, proprietary fonts excluded (B Nazanin, IRANSansX), handwriting-like counted as *style* not as writing | `pending` |
| D31 | Keep the 600-page P2 design on file while the first step runs 300 pages | `pending` |
| D32 | 100 % page pass at 20 s/page, defined as a rendering-integrity check (word-level GT is machine-verified) and not as full text verification | `pending` |
| D33 | Licence CC-BY-NC-4.0 with commercial licensing on request; sealed split withheld rather than licensed; three closed-model questions answered in the spec §9 | `pending` |
| D34 | Hand-writing protocol: our template, ≥ 8 named writers, 8 min/page fidelity check, never described as real documents | `pending` |
| D35 | Sealed test as a predictions-only scoring service, 1 scoring run per model | `pending` |

## 6. Gate-1 decisions (already approved, 2026-11-17)

Approval record: `docs/reports/2026-11-17_literature_review.md` §11.

| ID | Decides |
|---|---|
| T1 | Capability set C1–C6; formula and chart deferred |
| T2 | Three-condition design (P0 clean / P1 degraded / P2 real capture) mandatory |
| T3 | Domain weighting: administrative + forms + banking + financial ≈ 46 % |
| T4 | Handwriting deferred at Gate 1 (later reversed by D12/D34) |
| T5 | Diagnostic attribute list (Axis D) |
| T6 | Persian-language venue search before any novelty claim |
| T7 | Sealed/gated test set as the publication model if no public clearance exists |
| P1 | Strict plus L2 normalisation ladder, both reported |
| P2 | Grapheme-cluster CER as a companion metric |
| P3 | Frozen Markdown composite 0.4 / 0.3 / 0.3 |
| P4 | `marginal_note` and `stamp`/`signature` as first-class layout classes |
| P5 | `digit_swap_rate` and `unit_match` error classes |
| P6 | Bootstrap-over-documents confidence intervals |
| P7 | Binding "no comparison" table (cross-metric comparisons are refused in writing) |

---

## 7. Where each document is

Everything below `docs/` is git-ignored by the Gate-0 decision and therefore invisible in
the GitHub repository. It exists on disk at `/home/shahriar/Documents/P-OCR-VL/docs/`.

| File | Contains |
|---|---|
| `docs/reports/2026-11-16_project_initialization.md` | Gate 0, Q1–Q11, commit-policy origin |
| `docs/research/{search_log,benchmark_taxonomy,metric_protocol,persian_arabic_transfer_analysis}.md` | Gate 1, evidence tiers, T1–T7, P1–P7 |
| `docs/reports/2026-11-17_literature_review.md` | Gate-1 report, §11 approval record |
| `docs/dataset/benchmark_spec_v1.md` | Gate 2, D1–D12 + D30–D35 |
| `docs/dataset/{label_ontology,annotation_handbook,qa_plan,data_card}_v1.md` | Gate-2 deliverables |
| `docs/dataset/content_register_spec_v1.md` | D13–D18, register axes, lexicon seeds |
| `docs/dataset/handwriting_capture_protocol_v1.md` | D34 |
| `docs/dataset/content_generation_pipeline_v1.md` | D25–D29, throughput arithmetic |
| `docs/reports/2026-11-17_adr-001_runtime_architecture.md` | D19–D24, hosting |
| `docs/reports/2026-11-18_nvidia_model_probe.md` | model availability and speed measurement |

Machine-readable twins (tracked in git): `configs/benchmark/benchmark_v1.yaml`,
`configs/annotation/ontology_v1.yaml`, `configs/content/register_v1.yaml`,
`configs/content/generation_v1.yaml`, `configs/content/fonts_v1.yaml`.

Verify any count in them with `python scripts/validate_benchmark_config.py`.