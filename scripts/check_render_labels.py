"""Assert the D46 invariant on rendered pages: no scaffolding text ever ships.

The user's condition (2026-11-19/20) was explicit: element labels are acceptable while
they are *only* visible to a reviewer, and unacceptable the moment they appear on a
rendered page. This script is that acceptance check, run over a render directory:

    python scripts/check_render_labels.py --render artifacts/render_test/<UTC>

For every ``pdfs/<unit>.pdf`` it extracts the page text and looks for

1. an element label of the unit's **own** domain, in scaffolding position — a block that
   *starts* with the label followed by a separator («تیتر اصلی پوستر: ...»). A bare
   occurrence is not a leak: «روش» (method) is the real heading of an academic paper and
   «جمع کل» (grand total) the real footer of an invoice. Those are reported separately as
   informational, because the guideline they encode — a Persian reader must see a
   document, not our prompt — is about *position*, not vocabulary.
2. that vocabulary comes from ``configs/content/prompts_v1.yaml``
   (``domain_labels.*.element_labels``), the only place a label is shown to a model;
   ``register_v1.yaml`` holds English element *ids*, not Persian labels.
3. raw markup the model may have produced (``**``, backticks, ``__``),
4. the literal ``[FIG:`` placeholder (the caption must have been replaced by a figure).

Checking a unit against its own domain's list is what makes the signal usable: with the
global union, an administrative letter containing «هدف» (aim) was reported as a label
mention merely because some *other* domain calls a block that. Cross-domain collisions
are noise, so they are ignored.

Requires the ``render`` extra (PyMuPDF), so it is a script and not a test: CI installs
only the ``dev`` extra and must stay free of weasyprint/PyMuPDF.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402
import yaml  # noqa: E402

MARKUP_RE = re.compile(r"\*\*|\`|__")

# A *scaffolding* leak is a label in the position our prompt used for a label: at the
# start of a block, followed by a separator («تیتر اصلی پوستر: ...»). Words such as
# «جمع کل» (grand total), «روش» (method) or «لید» (lead) are also legitimate document
# vocabulary, so a bare occurrence proves nothing and is reported separately.
SCAFFOLD_TEMPLATE = r"^[\s\u200c]*{label}[\s\u200c]*[:\uff1a\u061b\u060c\-\u2013\u2014]"


def element_labels(config: Path) -> dict[str, list[str]]:
    """Per-domain Persian element labels, each list longest-first.

    Longest-first matters because labels are matched as substrings: «تیتر اصلی پوستر»
    must win over «تیتر», otherwise every hit is also reported as its own prefix.
    """
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for domain, entry in (data.get("domain_labels") or {}).items():
        names = {n for n in (entry.get("element_labels") or {}).values() if n}
        if names:
            out[str(domain)] = sorted(names, key=len, reverse=True)
    return out


def unit_domain(unit_id: str) -> str:
    """``financial_invoice_0007`` -> ``financial_invoice`` (domains contain underscores)."""
    head, _, tail = unit_id.rpartition("_")
    return head if tail.isdigit() and head else unit_id


def scan(render: Path, labels: dict[str, list[str]]) -> dict[str, object]:
    pdfs = sorted((render / "pdfs").glob("*.pdf"))
    union = sorted({n for names in labels.values() for n in names}, key=len, reverse=True)
    cache: dict[str, list[tuple[str, re.Pattern[str]]]] = {}

    def patterns_for(domain: str) -> list[tuple[str, re.Pattern[str]]]:
        if domain not in cache:
            names = labels.get(domain) or union
            cache[domain] = [
                (name, re.compile(SCAFFOLD_TEMPLATE.format(label=re.escape(name))))
                for name in names
            ]
        return cache[domain]

    hard: list[dict[str, object]] = []
    informational: list[dict[str, object]] = []
    for pdf in pdfs:
        doc = pymupdf.open(pdf)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        domain = unit_domain(pdf.stem)
        own = patterns_for(domain)
        leaks = [name for name, rx in own if rx.search(text)]
        if leaks:
            hard.append({"unit_id": pdf.stem, "domain": domain, "scaffold_labels": leaks})
            continue
        mentioned = [name for name, _ in own if name in text]
        markup = sorted(set(MARKUP_RE.findall(text)))
        soft: dict[str, object] = {"unit_id": pdf.stem}
        if mentioned:
            soft["labels_as_document_words"] = mentioned
        if markup:
            soft["markup"] = markup
        if "[FIG:" in text:
            soft["placeholder"] = True
        if len(soft) > 1:
            informational.append(soft)
    return {
        "render_dir": str(render),
        "n_pages_scanned": len(pdfs),
        "scaffold_hits": hard,
        "informational": informational,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D46 label-leak acceptance check")
    ap.add_argument("--render", required=True, help="render directory with pdfs/")
    ap.add_argument(
        "--prompts",
        default="configs/content/prompts_v1.yaml",
        help="config holding the Persian element-label vocabulary",
    )
    ap.add_argument("--json", dest="json_out", help="write the full report here")
    args = ap.parse_args(argv)

    render = Path(args.render)
    if not (render / "pdfs").is_dir():
        print(f"no pdfs/ under {render}", file=sys.stderr)
        return 2
    labels = element_labels(Path(args.prompts))
    report = scan(render, labels)
    report["n_domains"] = len(labels)
    report["n_labels"] = sum(len(v) for v in labels.values())

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"pages scanned : {report['n_pages_scanned']}")
    print(f"labels checked: {report['n_labels']} across {report['n_domains']} domain(s)")
    hard = report["scaffold_hits"]
    info = report["informational"]
    if not hard:
        print(
            "scaffolding   : NONE — no page starts a block with one of its own labels + separator"
        )
    else:
        print(f"SCAFFOLDING LEAK on {len(hard)} page(s):")
        for entry in hard[:40]:
            print(f"  {entry}")
    if info:
        print(
            f"note          : {len(info)} page(s) use a domain label as ordinary document text"
        )
        for entry in info[:6]:
            print(f"  {entry}")
        if len(info) > 6:
            print(f"  … {len(info) - 6} more (see --json)")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
