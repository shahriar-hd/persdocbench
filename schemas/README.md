# Data contract

JSON Schemas describing every artifact PersDocBench produces. They are the **single
source of truth**: `src/persdocbench/schemas/` mirrors them as typed models, and
`tests/contract/` validates them in CI.

| Schema | Describes |
|---|---|
| `page-metadata.schema.json` | one page's provenance: content origin, render origin, capture method, geometry |
| `annotations.schema.json` | layout blocks, reading order, raw and normalised text |
| `table.schema.json` | table grid with `rowspan`/`colspan`, header rows, cell boxes |
| `understanding.schema.json` | document-level questions, answers, evidence spans, answerability |
| `run-manifest.schema.json` | provenance of any produced artifact: code, config, model, condition, inputs |

Rules:

1. Every schema carries `$id` and `$schema` (draft 2020-12).
2. Adding a required field is a **breaking** change: bump `schema_version` and provide a
   migration note. Optional additions are non-breaking.
3. `text_raw` and `text_normalized` are separate fields by design. The raw field is what
   the pixels show; the normalised field is the evaluation form. Neither may be derived
   silently from the other.
4. Provenance axes are separate fields, never a single combined label.
