"""PersDocBench — a reproducible benchmark for Persian document AI.

Stage-aligned package layout. Each subpackage corresponds to one pipeline
stage and reads only the previous stage's *accepted* output, writing a new
immutable, content-hashed directory.

Pipeline order
--------------
    content      -> plaintext source generation with provenance
    rendering    -> HTML/CSS -> raster with DOM-measured ground truth
    augmentation -> physical capture simulation (opt-in, seeded, recorded)
    annotation   -> pre-annotation, curation, adjudication
    evaluation   -> task metrics and the shared scoring protocol
    adapters     -> one interface per model family, never one per benchmark run
    training     -> fine-tuning research on sealed-out evaluation data
    studio       -> Dataset Studio desktop application

See ``docs/reports/`` for the approved architecture record.
"""

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
