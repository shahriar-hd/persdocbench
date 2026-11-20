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
| D2 | Domain weighting: administrative cluster (letter, form, banking, invoice) ≈ 46 % | `answered` **final** 2026-11-19 (user) — **equal weight per domain**: the 46 % aggregate is dropped and the user's ~20 % counter-suggestion was "a random number". 1,500 / 16 = 93.75 → twelve domains get 94 pages, the four cluster domains get 93; cluster lands at 372/1500 = 0.248, every domain ≤ 0.063. `domain_groups.administrative_cluster_share_target: 0.25` |
| D3 | Script-style mix and the 8 % Nastaliq cap | `answered` — cap kept; **font inventory expanded to ≥ 30 families** (D30) |
| D4 | P2 real-capture size | `answered` 2026-11-18 — **300 pages** (400 images), not 600/1,200 |
| D5 | Two P1 variants (photometric vs geometric) instead of one degraded track | `answered` 2026-11-19 (user) — **keep both variants, scored separately** (photometric P1a, geometric P1b); OOD sensitivity differs by corruption family (RVL-CDIP-OOD, Larson et al., arXiv:2210.07448, T2) |
| D6 | Splits 20 / 40 / 40 (dev / public test / sealed test) | `answered in principle` — player asked what "sealed" means; explained as D6/D35, publication model still `pending` |
| D7 | Binding leakage controls: template-disjoint splits, pHash ≤ 6, MinHash ≤ 0.85, dedup by document | `answered` 2026-11-19 — **binding**; enforced in the split generator and `validate_benchmark_config.py` |
| D8 | 3,000 VQA items with the stated category mix | `answered` 2026-11-19 — **3,000 kept**; extended by D37 (300 in-page-image pages, 60 image-targeting items) |
| D9 | Dataset licence and access model | `answered` 2026-11-18 — **CC-BY-NC-4.0 + commercial licence on request** (D33) |
| D10 | Annotation budget and verification sampling | `superseded` by D32 |
| D11 | Arabic parallel control stratum (100 units, outside the headline) | `answered` 2026-11-19 — **kept and extended**: 100 units, `language_units: {fa: 40, ar: 60}`; the fa leg is the same-script control |
| D12 | Whether handwriting enters v1 | `answered` 2026-11-18 — **in, as a capture mode only** (D34) |

## 2. Content register (Gate 3 input)

Reasoning: `docs/dataset/content_register_spec_v1.md` §7 · profile:
`configs/content/register_v1.yaml`

| ID | Decides | Status |
|---|---|---|
| D13 | `subject_field` as an axis orthogonal to `domain` (7 fields) — how specialist prose enters without an 11th domain | `answered` 2026-11-19 — **expanded to 9 fields** (+ computer_science, agriculture_environment) and **12 domains** (+ medical_record, court_judgment) = 108 register cells |
| D14 | `register` axis with a 60 / 25 / 12 / 3 mix; `lay_explainer` banned as a defect | `answered` 2026-11-19 — mix kept; `lay_explainer` stays a hard defect (R2) |
| D15 | A banned-pattern hit is a hard generation failure, not a warning | `answered` 2026-11-19 (implemented) — reject with gate report persisted in `rejects.jsonl` |
| D16 | Threshold calibration on 15 samples × 70 cells (1,050), 25th percentile per register tier | `answered` 2026-11-19 — calibration plan now 15 × **108 cells** (12 domains × 9 fields) = 1,620 samples |
| D17 | Label register per content unit and break results down by it | `answered` 2026-11-19 (implemented) — `register` in every plan/accept/reject record |
| D18 | Medical/biology stays a subject field, not an 11th domain | `answered` 2026-11-19 — medicine_biology stays a **subject field** (share 0.13); the *domain* medical_record was added separately under D13 — field and domain are orthogonal |

## 3. Runtime architecture (ADR-001)

Reasoning: `docs/reports/2026-11-17_adr-001_runtime_architecture.md` §7–8

| ID | Decides | Status |
|---|---|---|
| D19 | Three planes: control, compute, GPU | `answered` 2026-11-19 — three planes approved |
| D20 | Control plane on Cloudflare Workers + D1 + R2 (free tier constraints respected) | `answered` 2026-11-19 — control plane approved **for the ~250 k generation**; benchmark 6 k stays local (D27) |
| D21 | Canonical rendering on GitHub Actions with pinned fonts; **no** Cloudflare Browser Rendering | `answered` 2026-11-19 — rendering runs on the **user's machine with the user's existing fonts** (not GH Actions) |
| D22 | Bulk images in a **private** Hugging Face dataset repo, not R2 | `answered` 2026-11-19 — user `.env` key; **benchmark data repo private, 250 k dataset repo public**. Still blocked: HF token expired → `hf auth login` |
| D23 | GPU plane chosen after inspecting molab / "morino" | `answered` 2026-11-19 — molab is the **marimo notebook** service (https://molab.marimo.io/notebooks): up to 1.5 h RTX6000Pro sessions, reset after 1.5 h, resumable in batches; checkpoints + data in Google Drive |
| D24 | Release artefacts mirrored to Zenodo/OSF | `answered` 2026-11-19 (default) — mirror at release time, after licence verification |

## 4. Content-generation pipeline

Reasoning: `docs/dataset/content_generation_pipeline_v1.md` §9 · profile:
`configs/content/generation_v1.yaml`

| ID | Decides | Status |
|---|---|---|
| D25 | Pipeline shape: idempotent request ids, append-only ledger, gates that report and never rewrite | `answered` 2026-11-19 (implemented, committed `c0d8d1f`) |
| D26 | Key policy: one key at the documented rate for the 6,000-request batch; multi-key aggregation recorded as a compliance fact | `answered` 2026-11-19 (implemented + measured B1–B5) — service defaults to 1 worker; 4-key aggregation recorded (fingerprints in `keys.py` docstring) |
| D27 | Hosting: local script for the batch; Oracle Always-Free ARM VM for 200 k; Cloudflare is control plane only | `answered` 2026-11-19 — local supervised service (`contentd.py`) for the 6 k; Cloudflare free tier for the 250 k generation (Oracle dropped) |
| D28 | Accepted outputs as sharded JSONL + manifest; rejects are a first-class artefact | `answered` 2026-11-19 (implemented) — `shards/shard-*.jsonl` + manifest; `rejects.jsonl` carries the gate report |
| D29 | `provider.model` set explicitly per run; no default, no guessing | `answered` 2026-11-19 (implemented) — `model_candidates` round + measured probe ranking (`docs/reports/2026-11-18_nvidia_model_probe.md`); `never_use` block |

## 5. Decisions raised by the 2026-11-18 answers (new)

Reasoning: this file's linked documents; config in `configs/content/fonts_v1.yaml`,
`configs/benchmark/benchmark_v1.yaml`.

| ID | Decides | Status |
|---|---|---|
| D30 | Font inventory: ≥ 30 licence-verified Persian families, ≥ 4 handwriting-like, proprietary fonts excluded (B Nazanin, IRANSansX), handwriting-like counted as *style* not as writing | `answered` 2026-11-18 — **43 families** (≥ 30), 4 handwriting-like; licence records in `fonts_v1.yaml` |
| D31 | Keep the 600-page P2 design on file while the first step runs 300 pages | `answered` 2026-11-18 — 300-page P2 runs first; 600-page design stays on file |
| D32 | 100 % page pass at 20 s/page, defined as a rendering-integrity check (word-level GT is machine-verified) and not as full text verification | `answered` 2026-11-18 — approved as written |
| D33 | Licence CC-BY-NC-4.0 with commercial licensing on request; sealed split withheld rather than licensed; three closed-model questions answered in the spec §9 | `answered` 2026-11-18 — approved as written |
| D34 | Hand-writing protocol: our template, ≥ 8 named writers, 8 min/page fidelity check, never described as real documents | `answered` 2026-11-18 — approved as written (`handwriting_capture_protocol_v1.md`) |
| D35 | Sealed test as a predictions-only scoring service, 1 scoring run per model | `answered` 2026-11-18 — approved as written |

| ID | Decides | Status |
|---|---|---|
| D36 | CI pipeline: pinned `uv.lock` + `uv sync` so the cache hard-fail is gone | `answered` 2026-11-19 (implemented) — commit `c0d8d1f`, CI run `44488474045` green |
| D37 | VQA image-in-document: ~300 pages carry a subject-related figure inside the page; 60 of 3,000 items require reading the figure | `answered` 2026-11-19 (implemented at config+generator) — per-domain `image_share`; `[FIG: caption]` placeholder + caption in the text; figures are renderer-side (Gate 4), seeded placeholders now |
| D38 | Per-page bounding boxes rendered into a separate folder for visualisation | `answered` 2026-11-19, **revised 2026-11-19 (user): no word-level boxes** — render-time boxes are LINE / PARAGRAPH / FIGURE / TABLE / LAYOUT granularity (PyMuPDF dict blocks/lines + `find_tables`), overlay PNGs colour-code line=red, figure=blue, table=green; render-time boxes **never** benchmark GT |
| D39 | Test-then-scale: rendered samples per domain must be approved before the 6,000-sample run | `answered` 2026-11-19 (user) — render a **fresh per-domain test set**; on written approval, generate the 6,000 samples |
| D40 | Source of the real in-page images (Gate 4): user asked for *real* images attached to the text (e.g. a person exercising with a text title like «ورزش برای سلامتی»), not placeholder charts | `answered` 2026-11-19 — **Wikimedia Commons (licence-verified, per-image record) or the COCO dataset**; COCO images carry mixed Flickr licences so each COCO image still needs a per-image licence check before use; content layer keeps `[FIG: title]` placeholders until Gate-4 figure sourcing |
| D41 | Sample diversity (user feedback 2026-11-19): magazine was missing; only 1/52 test units had a real table | `answered` 2026-11-19 (implemented) — `magazine_article` domain added (13th, 60 pages / 0.04, image_share 0.60); `tables_expected` contract added to 6 table-native domains; `{table_line}` prompt hook; validator/tests updated |
| D42 | Preview format (user feedback 2026-11-19): current renders are plaintext previews only — same font, no LaTeX/effects; user is fine with plaintext and asked for markdown output instead of images | `answered` 2026-11-19 — `scripts/preview_markdown.py` writes markdown previews; rendered effects/layouts deferred to the user's explicit next step |
| D43 | Type range (user feedback 2026-11-19): wide range of document types — book, article, magazine, poster, website, even manga; not only simple pages | `answered` 2026-11-19 (implemented) — **16 domains** now: + `poster` (45 p, image_share 0.70), `website` (45 p, 0.30), `manga` (45 p, image_share 1.0, text-in-image); non-admin domains rescaled (academic 120, educational 120, legal 105, book 60, news 60, technical 60, medical 60, court 45, magazine 45); P2 quota 18 + 12 remainder; 144 register cells, calibration 2160; visual layouts (two-column, flowcharts, text-in-boxes, poster shape, manga bubbles) are Gate-4 steps the user will drive |
| D44 | ~50-sample type review: user wants ~50 different sample types to check before further steps | `in_progress` 2026-11-19 — merged markdown preview pack + rendered pages from the 51-unit batch (13 domains) and new-domain batch (poster/website/manga); thin domains to be topped up if the user asks |
| D45 | One page per document (user 2026-11-19): "I want all samples in one page; if the plaintext renders in two pages, pass the second page and only the first page approve — because each of the 6,000 images has unique content" | `answered` 2026-11-19 (implemented) — the renderer fits every document to **exactly one page** by walking a bounded typographic ladder (11pt/1.8cm → 8.5pt/1.2cm); content is **never truncated** (that would break text↔image alignment); a unit that cannot fit is flagged `one_page_ok: false` with `spilled_pages` and is never silently split into a second "document". `--multi-page` restores the old behaviour |
| D46 | Provider output must be **JSON**, not free prose (user 2026-11-19/20): table-native domains rendered with no table at all, and for posters/manga the model echoed our own element labels as literal page text ("تیتر اصلی پوستر: ...") | `answered` 2026-11-20 (implemented) — the prompt returns one JSON object with `blocks`; the element identity is the block `role` (English id from `register_v1.yaml`) and `text`/`value` hold content only, so a label cannot reach a page; `src/persdocbench/content/blocks.py` parses/decodes (fence-tolerant), flattens to text, markdown and prose; a leading echo of a known label is removed and **recorded** in `label_echo_stripped`; inline `**bold**`/backticks added when the model formats its answer are removed and counted in `markup_fields_stripped`; a label-prefixed **real sentence** is never edited, only flagged in `label_prefix_suspect`; malformed JSON is model-shaped: it retries and then falls through to the next candidate model, and never lands in `rejects.jsonl`; `--format text` keeps the legacy path |
| D47 | Two defects the D46 smoke run exposed (no user input needed; found while implementing D46) | `answered` 2026-11-20 (implemented) — (a) **a revoked key was killing units**: NVIDIA returns the same `403: authorization failed` for a revoked key and for a model the key is not entitled to, and the classifier treated both as "this model is forbidden" (a plain fatal), so every unit routed to the dead key died — 26 units lost in an 8-unit smoke run; the dispatcher now decides from evidence: an ambiguous entitlement 403 requeues on an untried key (nothing was evaluated), and the key is retired only once it has failed for two distinct models, or once it fails for a model that another key already served; (b) **JSON answers were truncated**: `max_tokens: 1400` ended the tail with `finish_reason=length`, raised to 2200 (structure and table rows cost tokens); evidence: `tests/unit/test_content_pipeline.py` (2 new), `tests/unit/test_content_blocks.py` (26), smoke run `data/interim/gen/jsonsmoke3-20261120T110129Z` (8 units → 8/8 resolved on the first model, 6 accepted / 2 rejected `banned_lay_pattern`, 2 invoices carrying a real table, 1 manga carrying a figure, 0 label leaks) |
| D48 | "6,000 unique contents" needs a measurement behind it (raised while the 6,000-unit run was going): the config declared a dedup contract but nothing implemented it | `answered` 2026-11-20 (implemented) — new `src/persdocbench/content/dedup.py` (stdlib only: 5-word shingles over folded text, 128-permutation MinHash, **exact Jaccard confirmation** before any pair is called a duplicate, per-cell comparison) + `scripts/audit_uniqueness.py` writing `uniqueness.md`/`uniqueness.json` into the run; reports the nearest-neighbour distribution as well as the threshold verdict (partial run: 1,819 records, 0 duplicates at 0.85, closest pair 0.286); 12 tests |
| D49 | `definition_copulas_fa` (`یعنی`, `به معنای`, `عبارت است از`, `تعریف میشود به`) is a **hard** reject, but the words are ordinary formal Persian — 98 of 168 rejects at +15 min of the 6,000-unit run were triggered by `یعنی` alone | `answered` 2026-11-20 (**user: keep it a hard reject**) — the copulas stay a per-hit content rejection; the accepted corpus is left as is. The 631 rejected units are preserved with full text and gate report, so a regeneration pass can replace them later under the same gate if the reject budget is revisited. Evidence for the cost of the rule: `legal_contract` 29.6 %, `book_literary` 16.5 %, `financial_invoice` 16.3 % rejected vs `academic_paper` 2.5 % |
| D50 | After the 6,000-unit run: 41 units did not resolve at all — `Invalid \escape` (33) and `Expecting ',' delimiter` (8) from the model's JSON | `answered` 2026-11-20 (implemented) — `blocks.repair_json` neutralises only two *syntax* defects (a backslash the JSON grammar forbids, a raw control character inside a string) and `extract_json_ex` marks the result `json_repaired: true` on the record, so a repaired unit can never pass as a verbatim parse; it recovers ~33 of the 41 (80 %) — the delimiter cases need the model's structure rewritten, which we refuse to do. 5 new tests |
| D51 | The 6,000-unit run finished: is the corpus usable for the review gate? | `answered` 2026-11-20 — **5,326 accepted / 631 rejected / 43 failed** (5,957 units resolved; reject rate 10.6 %). Every table-native unit carries a real table (2,091 table blocks, median 3 rows x 3 cols, no empty header or row) and all 1,728 image slots carry a `[FIG: caption]` for the D40 image source. `scripts/check_render_labels.py`: **0 scaffolding leaks** on the 80-page review pack. `uniqueness`: 0 near-duplicates at 0.85, closest pair **0.286** over 5,326 documents. Soft flags (advisory, uncalibrated by design) 72 % of units — the Gate-3 calibration pilot is what fixes the thresholds. 135 of 144 register cells reached. Remaining blockers are D49 (rejects) and the review pack sign-off |
| D52 | Renderer-side verification: does a rendered page really carry the content we recorded? | `answered` 2026-11-20 (implemented) — `find_tables` dimension agreement is the check that works: **80/80 units** have exactly the number of tables the record has (30 table-bearing units, 4x4/4x3/2x4 dims matching header+rows). Substring matching against PDF text **must not** be used as the criterion (see D53) |
| D53 | The PDF text layer of Sahel/Vazirmatn documents reads «کاال» for «کالا» (WeasyPrint 70.0 font-subset ToUnicode maps the lam-alef ligature to the reversed base pair); `full_fonts=True` fixes the ligature but switches the whole layer to Arabic presentation forms (U+FExx) and grows PDFs 5-30x | `answered` 2026-11-20 — keep default subsetting (small PDFs, canonical codepoints) and treat the PDF text layer as an **advisory convenience artefact, never ground truth**: GT comes from the record JSON and the OCR task reads the PNG, where the ligature renders correctly. Checks that read PDF text are limited to scaffolding patterns (no lam-alef) and to bidi-insensitive structure (`find_tables`); documented in the pipeline doc. Verified on four families: Nazli and Noto Naskh Arabic subset correctly, Sahel and Vazirmatn do not |

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
| `data/interim/gen/` | pilot content runs B1–B5 + 60-unit test batch (accepted shards, rejects, ledgers) — git-ignored |
| `artifacts/render_test/` | smoke-test rendered pages, bboxes, overlays for the D39 approval gate — git-ignored |
| `artifacts/preview/` | markdown preview pack (D42/D44): one `.md` per accepted unit + `index.md`, plaintext only; merged packs cover all 16 domains — git-ignored |

Machine-readable twins (tracked in git): `configs/benchmark/benchmark_v1.yaml`,
`configs/annotation/ontology_v1.yaml`, `configs/content/register_v1.yaml`,
`configs/content/generation_v1.yaml`, `configs/content/fonts_v1.yaml`.

Verify any count in them with `python scripts/validate_benchmark_config.py`.