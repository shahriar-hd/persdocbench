# PersDocBench

A reproducible benchmark for **Persian document AI** — page OCR, layout analysis, reading
order, table structure, document parsing (Markdown/HTML) and document understanding —
built so that reported numbers can be traced to a specific image, a specific annotation,
a specific model revision and a specific inference condition.

> **Status: pre-alpha, Gate 0.** The repository contains scaffolding only. No data has
> been generated, no model has been benchmarked. The research protocol and dataset
> specification are being developed in sequence; see the roadmap below.

---

## Why this exists

Persian document research is fragmented: resources are scattered across Arabic-script
work, evaluation protocols differ per paper, and Persian-specific normalisation is
usually unreported. Two failure modes are endemic and this project is designed against
both:

1. **Unreported normalisation.** Persian text differs on Arabic vs. Persian `ی`/`ک`,
   Persian vs. Latin digits, ZWNJ, diacritics and tatweel. A single CER figure can be
   dominated by Unicode convention rather than by misreading. This benchmark reports
   **strict and normalised scores separately**, and never silently folds a semantic
   distinction away.
2. **Synthetic-only evaluation.** A model fine-tuned on one synthetic generator can reach
   a very low CER on that generator's test split while barely improving on a different
   distribution. Evaluation therefore separates *content origin*, *render origin* and
   *capture method*, and keeps a sealed, human-annotated test set out of every training
   and tuning loop.

## Scope

| Track | Description |
|---|---|
| Text recognition | Page- and line-level OCR with strict / normalised / digit metrics |
| Layout analysis | Element detection with per-class precision/recall and mAP |
| Reading order | RTL-correct ordering, scored by normalised edit distance |
| Table structure | Cell grid with `rowspan`/`colspan`, HTML and tree-edit-distance scoring |
| Document parsing | Image → Markdown, scored structurally and textually |
| Document understanding | Extractive, multi-page and unanswerable VQA with evidence spans |

Formula and chart parsing are **deferred** beyond v1.

## Repository layout

```text
.
├── src/persdocbench/     # stage-aligned package
│   ├── common/           # ids, hashing, IO, provenance, seeding, logging
│   ├── schemas/          # typed models mirroring /schemas
│   ├── content/          # Stage C: Persian source-text generation
│   ├── rendering/        # Stage D: HTML/CSS -> raster, DOM ground truth
│   ├── augmentation/     # Stage D: capture simulation, seeded and recorded
│   ├── annotation/       # Stage E: pre-annotation, curation, adjudication
│   ├── studio/           # Stage F: Dataset Studio (PySide6)
│   ├── evaluation/       # Stage G: metrics and the shared scoring protocol
│   ├── adapters/         # Stage G/H: one adapter per model family
│   └── training/         # Stage H: LoRA/QLoRA ablations
├── schemas/              # JSON Schemas — the data contract
├── configs/              # versioned stage configurations
├── tests/                # unit / integration / data-contract
└── docs/                 # internal; not tracked in this repository
```

`docs/`, `data/`, `old/`, `skills/`, `.pi/` and `.claude/` are intentionally
**git-ignored**: they hold internal reports, research notes, third-party reference code,
agent configuration and datasets. They are not part of the published artifact.

## Data contract

One page is described by a set of co-located artifacts sharing a `page_id`:

```text
page_id/
├── image.png               immutable raster, sha256 recorded
├── page-metadata.json      content_origin x render_origin x capture_method
├── annotations.json        blocks -> lines -> spans, text_raw + text_normalized
├── table.json              cell grid with rowspan/colspan (optional)
├── document.md             parsed-document ground truth
└── render-recipe.json      template, fonts, seed, augmentation profile
```

Provenance axes are never collapsed into one label. A synthetic page captured through a
phone remains **synthetic content** with a **physical capture path**, and results are
reported cross-tabulated on both.

Every produced artifact also carries a run manifest: code commit, config hash, seed,
model revision, inference condition and input checksums. No result is reportable
without it.

## Roadmap

| Gate | Stage | Output |
|---|---|---|
| 0 | Initialisation | repository, licence, schemas, scaffolding — **done** |
| 1 | Literature review | taxonomy, metric protocol, transfer analysis, citation registry |
| 2 | Dataset specification | count matrix, split policy, annotation ontology, licence table |
| 3 | Content generation | prompt templates, model set, pilot batch |
| 4 | Rendering & augmentation | template matrix, sample gallery, capture families |
| 5 | Annotation | handbook, pre-annotation adapters, adjudication |
| 6 | Dataset Studio | browse, overlay, edit, sealed read-only mode |
| 7 | Benchmark runner | adapter interface, metrics, baseline leaderboard |
| 8 | Fine-tuning research | domain-balanced mixture, controlled ablations |
| 9 | Reporting | benchmark card, dataset card, reproducibility record |

Each gate ends in a written deliverable and an explicit review before the next begins.

## Install

Requires Python 3.11 or 3.12. Python 3.13+ currently lacks wheels for parts of the ML
stack and is not supported.

```bash
uv sync --extra dev            # core + dev tooling
uv sync --extra dev --extra render --extra content
```

## Licence

Code: [Apache-2.0](LICENSE).

Benchmark data, annotations and synthetic corpora are **not** covered by this licence and
will be released under a separate terms document once the ingredient licence audit is
complete. Nothing in this repository grants rights to redistribute third-party content.
