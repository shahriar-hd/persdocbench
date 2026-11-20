#!/usr/bin/env python3
"""Smoke-test renderer for the Gate-3 test gate (D38/D39).

Takes an accepted content run (``shards/accepted-with-flags.jsonl`` or
``shards/shard-*.jsonl``) and renders a sample of units to real pages:

    text -> HTML (rtl, tables, headings) -> WeasyPrint PDF -> PyMuPDF PNG pages
    -> per-page line/paragraph/figure/table boxes (JSON) + overlay PNGs (separate folder)

[FIG: caption] placeholders become seeded placeholder charts so the D37
image-in-document pages are visible before the Gate-4 figure generator exists.
The full renderer (Gate 4) replaces this script and draws subject-specific
figures from the fonts_v1 pool with a per-family usage log.

Usage
-----
    python scripts/render_samples.py --run data/interim/gen/<run> [--per-domain 5]
        [--dpi 200] [--font Sahel] [--image-slots-only]
        [--out artifacts/render_test/<stamp>]
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from persdocbench.content.markdown import (  # noqa: E402
    is_separator_row,
)

try:
    import fitz  # PyMuPDF
    import weasyprint
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "renderer needs weasyprint, pymupdf and pillow: "
        f"uv pip install --python <venv> weasyprint pymupdf pillow ({exc})"
    ) from None


def font_file(family: str) -> str | None:
    """Resolve a fontconfig family to a font file for Pillow figure captions."""
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", family], capture_output=True, text=True
        ).stdout.strip()
        return out or None
    except FileNotFoundError:
        return None


# Table-separator logic lives in the package (mypy-strict, dependency-free) so the
# renderer and the markdown preview cannot drift apart; see the 2026-11-19 defect.
_IS_SEPARATOR_ROW = is_separator_row


def figure_png(
    caption: str, seed: int, out_path: Path, font_file_path: str | None
) -> None:
    """Seeded placeholder bar chart. Gate 4 replaces this with real figures."""
    rng = random.Random(seed)
    w, h = 640, 300
    img = Image.new("RGB", (w, h), (250, 250, 250))
    dr = ImageDraw.Draw(img)
    bars = [rng.randint(20, 95) for _ in range(8)]
    xs = 60
    for i, v in enumerate(bars):
        bh = int(v * 2.2)
        x0 = xs + i * 62
        dr.rectangle([x0, h - 40 - bh, x0 + 42, h - 40], fill=(70 + i * 12, 120, 190))
        dr.text((x0 + 12, h - 34), str(i + 1), fill=(40, 40, 40))
    dr.line([(40, h - 40), (w - 30, h - 40)], fill=(60, 60, 60), width=2)
    if font_file_path:
        fnt = ImageFont.truetype(font_file_path, 20)
        dr.text((w // 2, 12), caption[:60], fill=(30, 30, 30), anchor="ma", font=fnt)
    else:
        dr.text((w // 2, 12), "fig " + str(seed)[:6], fill=(30, 30, 30))
    img.save(out_path, "PNG")


def text_to_html(text: str, unit_id: str, fig_dir: Path) -> tuple[str, int]:
    """Very small markdown-ish -> HTML. Returns (html_body, figure_count)."""
    lines = text.splitlines()
    out: list[str] = []
    figs = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            # user feedback 2026-11-19: blank lines caused big paragraph gaps;
            # collapse them instead of emitting empty <p> blocks.
            i += 1
            continue
        if stripped.startswith("```"):
            # fence blocks: render as <pre> until the closing fence
            buf = [escape(lines[i][3:])]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre>" + "\n".join(buf) + "</pre>")
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not _IS_SEPARATOR_ROW(cells):
                    rows.append(cells)
                i += 1
            if rows:
                head = rows[0]
                body = rows[1:]
                out.append(
                    "<table><thead><tr>"
                    + "".join(f"<th>{escape(c)}</th>" for c in head)
                    + "</tr></thead><tbody>"
                    + "".join(
                        "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>"
                        for r in body
                    )
                    + "</tbody></table>"
                )
            continue
        if stripped.startswith("####"):
            out.append(f"<h4>{escape(stripped[4:].strip())}</h4>")
            i += 1
            continue
        if stripped.startswith("###"):
            out.append(f"<h3>{escape(stripped[3:].strip())}</h3>")
            i += 1
            continue
        if stripped.startswith("##"):
            out.append(f"<h2>{escape(stripped[2:].strip())}</h2>")
            i += 1
            continue
        if stripped.startswith("#"):
            out.append(f"<h1>{escape(stripped[1:].strip())}</h1>")
            i += 1
            continue
        if "[FIG:" in stripped:
            start = stripped.find("[FIG:")
            end = stripped.find("]", start)
            caption = stripped[start + 5 : end].strip() if end > start else ""
            fig_path = fig_dir / f"{unit_id}_f{figs}.png"
            figure_png(caption, figs + 1, fig_path, font_file_path())
            out.append(
                f'<div class="figure"><img src="{fig_path.name}" alt="{escape(caption)}"/>'
                f'<div class="figcaption">{escape(caption)}</div></div>'
            )
            figs += 1
            i += 1
            continue
        out.append(f"<p>{escape(stripped)}</p>")
        i += 1
    return "\n".join(out), figs


_FONT_CACHE: dict[str, str | None] = {}


def font_file_path() -> str | None:
    fam = _FONT_CACHE.get("family")
    if fam not in _FONT_CACHE:
        _FONT_CACHE["family"] = fam
        _FONT_CACHE["path"] = font_file(fam) if fam else None
    return _FONT_CACHE["path"]


def _css(family: str, font_pt: float, margin_cm: float) -> str:
    return f"""
    @page {{ size: A4; margin: {margin_cm}cm; }}
    body {{ font-family: "{family}", sans-serif; direction: rtl; text-align: right;
           font-size: {font_pt}pt; line-height: 1.55; color: #111; }}
    p {{ margin: 2pt 0; }}
    h1 {{ font-size: {font_pt * 1.36:.1f}pt; }} h2 {{ font-size: {font_pt * 1.18:.1f}pt; }}
    h3 {{ font-size: {font_pt * 1.09:.1f}pt; }}
    table {{ border-collapse: collapse; width: 100%; margin: 6pt 0; }}
    th, td {{ border: 0.6pt solid #444; padding: 3pt 5pt;
             font-size: {font_pt * 0.86:.1f}pt; }}
    th {{ background: #eee; }}
    .figure {{ text-align: center; margin: 8pt 0; }}
    .figure img {{ max-width: 88%; border: 0.6pt solid #999; }}
    .figcaption {{ font-size: {font_pt * 0.86:.1f}pt; color: #333; margin-top: 2pt; }}
    pre {{ direction: ltr; text-align: left; background: #f4f4f4; padding: 6pt;
          font-size: {font_pt * 0.77:.1f}pt; border: 0.4pt solid #bbb; }}
    """


# D45 (user, 2026-11-19): every document is ONE page — 6,000 images with 6,000
# unique documents, never a page-2 continuation of the previous document. The
# content is never truncated (that would break text <-> image alignment), so the
# page is fitted typographically: a bounded ladder of (font size, margin).
_FIT_LADDER: tuple[tuple[float, float], ...] = (
    (11.0, 1.8),
    (10.5, 1.6),
    (10.0, 1.5),
    (9.5, 1.4),
    (9.0, 1.3),
    (8.5, 1.2),
)


def _render_pdf(
    html: str, family: str, font_pt: float, margin_cm: float, base_url: Path | None
) -> bytes:
    doc = (
        f"<html><head><meta charset='utf-8'><style>"
        f"{_css(family, font_pt, margin_cm)}</style></head><body>{html}</body></html>"
    )
    return weasyprint.HTML(
        string=doc, base_url=str(base_url) if base_url else None
    ).write_pdf()


def _fit_one_page(
    html: str, family: str, base_url: Path | None
) -> tuple[bytes, float, float, int, int]:
    """Render so the document occupies exactly one page (D45).

    Walk the bounded typographic ladder and take the first setting whose PDF has
    a single page. Content is never altered. Returns
    ``(pdf_bytes, font_pt, margin_cm, spilled_pages, attempts)`` where
    ``spilled_pages`` is 0 on success and the page count of the *default*
    setting when no ladder rung fits (the unit is then flagged, not silently cut).
    """
    last: bytes = b""
    first_spill = 0
    for attempts, (font_pt, margin_cm) in enumerate(_FIT_LADDER, start=1):
        pdf = _render_pdf(html, family, font_pt, margin_cm, base_url)
        with fitz.open(stream=pdf, filetype="pdf") as d:
            n = d.page_count
        if attempts == 1:
            first_spill = 0 if n == 1 else n
        if n == 1:
            return pdf, font_pt, margin_cm, 0, attempts
        last = pdf
    return (
        last,
        _FIT_LADDER[-1][0],
        _FIT_LADDER[-1][1],
        first_spill or 2,
        len(_FIT_LADDER),
    )


def render_one(
    rec: dict,
    out_dir: Path,
    *,
    dpi: int,
    family: str,
    html: str,
    base_url: Path | None = None,
    one_page: bool = True,
) -> dict:
    unit_id = str(rec["unit_id"])
    base = out_dir / "pages"
    bbox_dir = out_dir / "bboxes"
    overlay_dir = out_dir / "overlays"
    base.mkdir(parents=True, exist_ok=True)
    bbox_dir.mkdir(parents=True, exist_ok=True)
    overlay_dir.mkdir(parents=True, exist_ok=True)

    if one_page:
        pdf_bytes, fit_pt, fit_margin, spilled, attempts = _fit_one_page(
            html, family, base_url
        )
    else:
        fit_pt, fit_margin = _FIT_LADDER[0]
        pdf_bytes = _render_pdf(html, family, fit_pt, fit_margin, base_url)
        attempts, spilled = 1, 0

    pdf_path = out_dir / "pdfs" / f"{unit_id}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)

    doc_pdf = fitz.open(pdf_path)
    scale = dpi / 72.0
    pages_meta: list[dict] = []
    page_total = doc_pdf.page_count
    for pno, page in enumerate(doc_pdf):
        if one_page and pno > 0:
            # D45: one document = one image. Any spill is a fit failure that is
            # reported, never silently converted into a second "document".
            break
        pix = page.get_pixmap(dpi=dpi)
        png_path = base / f"{unit_id}_p{pno}.png"
        pix.pil_save(png_path)

        # D38 (revised 2026-11-19): render-time boxes are LINE / PARAGRAPH /
        # FIGURE / TABLE / LAYOUT granularity — never word-level. These are
        # visualisation aids, never benchmark GT (GT is annotation-time).
        lines_out: list[dict] = []
        paras: list[dict] = []
        figs: list[dict] = []
        for block in page.get_text("dict")["blocks"]:
            bx0, by0, bx1, by1 = block["bbox"]
            if block.get("type") == 1:
                figs.append(
                    {
                        "kind": "image",
                        "x": round(bx0 * scale, 2),
                        "y": round(by0 * scale, 2),
                        "w": round((bx1 - bx0) * scale, 2),
                        "h": round((by1 - by0) * scale, 2),
                    }
                )
                continue
            for line in block.get("lines", []):
                lx0, ly0, lx1, ly1 = line["bbox"]
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text:
                    continue
                lines_out.append(
                    {
                        "text": text,
                        "block": block["number"],
                        "x": round(lx0 * scale, 2),
                        "y": round(ly0 * scale, 2),
                        "w": round((lx1 - lx0) * scale, 2),
                        "h": round((ly1 - ly0) * scale, 2),
                    }
                )
            if block.get("lines"):
                para_text = "".join(
                    s["text"] for ln in block["lines"] for s in ln["spans"]
                ).strip()
                paras.append(
                    {
                        "block": block["number"],
                        "text": para_text[:80],
                        "x": round(bx0 * scale, 2),
                        "y": round(by0 * scale, 2),
                        "w": round((bx1 - bx0) * scale, 2),
                        "h": round((by1 - by0) * scale, 2),
                    }
                )
        tables = []
        try:
            for t in page.find_tables().tables:
                tx0, ty0, tx1, ty1 = t.bbox
                tables.append(
                    {
                        "rows": t.row_count,
                        "cols": t.col_count,
                        "x": round(tx0 * scale, 2),
                        "y": round(ty0 * scale, 2),
                        "w": round((tx1 - tx0) * scale, 2),
                        "h": round((ty1 - ty0) * scale, 2),
                    }
                )
        except Exception:
            tables = []
        (bbox_dir / f"{unit_id}_p{pno}.json").write_text(
            json.dumps(
                {
                    "unit_id": unit_id,
                    "page": pno,
                    "dpi": dpi,
                    "lines": lines_out,
                    "lines_n": len(lines_out),
                    "paragraphs": paras,
                    "paragraphs_n": len(paras),
                    "figures": figs,
                    "figures_n": len(figs),
                    "tables": tables,
                    "tables_n": len(tables),
                    "provenance": "render_test",
                    "bbox_source": "pymupdf dict blocks/lines + find_tables (render-time, not GT; D38 revision)",
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

        # overlay: line boxes (red), figure regions (blue), table regions (green)
        img = Image.open(png_path).convert("RGB")
        dr = ImageDraw.Draw(img)
        for ln in lines_out:
            dr.rectangle(
                [ln["x"], ln["y"], ln["x"] + ln["w"], ln["y"] + ln["h"]],
                outline=(230, 40, 40),
                width=2,
            )
        for fg in figs:
            dr.rectangle(
                [fg["x"], fg["y"], fg["x"] + fg["w"], fg["y"] + fg["h"]],
                outline=(30, 90, 230),
                width=3,
            )
        for tb in tables:
            dr.rectangle(
                [tb["x"], tb["y"], tb["x"] + tb["w"], tb["y"] + tb["h"]],
                outline=(30, 170, 70),
                width=3,
            )
        img.save(str(overlay_dir / f"{unit_id}_p{pno}.png"), "PNG")
        pages_meta.append(
            {
                "page": pno,
                "png": png_path.name,
                "lines": len(lines_out),
                "figures": len(figs),
                "tables": len(tables),
            }
        )
    doc_pdf.close()
    return {
        "unit_id": unit_id,
        "pdf": str(pdf_path.relative_to(out_dir)),
        "pages": pages_meta,
        "page_total": page_total,
        "one_page_ok": page_total == 1,
        "fit_font_pt": fit_pt,
        "fit_margin_cm": fit_margin,
        "fit_attempts": attempts,
        "spilled_pages": spilled,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run", required=True, help="content run directory")
    ap.add_argument("--per-domain", type=int, default=5)
    ap.add_argument("--domain", action="append", help="restrict domains (repeatable)")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--font", default="Sahel", help="fontconfig family for the page")
    ap.add_argument(
        "--image-slots-only",
        action="store_true",
        help="render only units whose plan flagged image_slot (D37)",
    )
    ap.add_argument(
        "--multi-page",
        action="store_true",
        help="allow more than one page per unit (default: fit to exactly one, D45)",
    )
    ap.add_argument(
        "--out", default=None, help="output dir (default artifacts/render_test/<stamp>)"
    )
    args = ap.parse_args(argv)

    run_dir = Path(args.run).resolve()
    flagged = run_dir / "shards" / "accepted-with-flags.jsonl"
    if flagged.is_file():
        recs = [
            json.loads(line)
            for line in flagged.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        recs = []
        for shard in sorted((run_dir / "shards").glob("shard-*.jsonl")):
            recs += [
                json.loads(line)
                for line in shard.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    if not recs:  # worker subdirs: <run>/w<k>/shards/*.jsonl
        for shard in sorted(run_dir.glob("w*/shards/*.jsonl")):
            if shard.name == "accepted-with-flags.jsonl":
                continue
            recs += [
                json.loads(line)
                for line in shard.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    if not recs:
        raise SystemExit(f"no accepted records in {run_dir}/shards")

    _FONT_CACHE["family"] = args.font
    if args.image_slots_only:
        recs = [r for r in recs if r.get("image_slot")]

    selected: list[dict] = []
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_domain[str(r["domain"])].append(r)
    domains = args.domain or sorted(by_domain)
    for d in domains:
        pool = by_domain.get(d, [])
        if not pool:
            print(f"  note: no accepted records for domain {d}")
            continue
        # prefer subject-field variety, then image slots, then arrival order
        pool = sorted(
            pool,
            key=lambda r: (
                sum(1 for x in selected if x["subject_field"] == r["subject_field"]),
                0 if r.get("image_slot") else 1,
                str(r["unit_id"]),
            ),
        )
        selected += pool[: args.per_domain]

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (
        Path(args.out) if args.out else ROOT / "artifacts" / "render_test" / stamp
    ).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    rendered = []
    for rec in selected:
        html, figs = text_to_html(str(rec["text"]), str(rec["unit_id"]), fig_dir)
        meta = render_one(
            rec,
            out_dir,
            dpi=args.dpi,
            family=args.font,
            html=html,
            base_url=fig_dir,
            one_page=not args.multi_page,
        )
        meta.update(
            {
                "domain": rec["domain"],
                "subject_field": rec["subject_field"],
                "register": rec["register"],
                "image_slot": rec.get("image_slot", False),
                "figures": figs,
            }
        )
        rendered.append(meta)
        flag = (
            ""
            if meta["one_page_ok"]
            else f"  !! ONE-PAGE FIT FAILED ({meta['spilled_pages']}p)"
        )
        print(
            f"  {rec['unit_id']:<28} {rec['domain']:<22} "
            f"{rec['subject_field']:<22} fig={figs} pages={meta['page_total']}"
            f" fit={meta['fit_font_pt']}pt@{meta['fit_margin_cm']}cm{flag}"
        )

    manifest = {
        "stamp": stamp,
        "run": str(run_dir),
        "font": args.font,
        "dpi": args.dpi,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "units_rendered": len(rendered),
        "units": rendered,
        "folders": {
            "pages": "pages/",
            "bboxes": "bboxes/",
            "overlays": "overlays/",
            "figures": "figures/",
            "pdfs": "pdfs/",
        },
        "note": "Smoke-test render (Gate-3 test gate). Bboxes are render-time "
        "extractions for visualisation only, never benchmark GT. "
        "Figures are seeded placeholders until the Gate-4 renderer.",
        "provenance": "synthetic_native",
    }
    (out_dir / "render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    failed = [u["unit_id"] for u in rendered if not u["one_page_ok"]]
    print(f"\nrendered {len(rendered)} units -> {out_dir}")
    print(
        f"  one page : {len(rendered) - len(failed)}/{len(rendered)}"
        + (f"   FIT FAILURES: {len(failed)}" if failed else "")
    )
    print("  pages   :", (out_dir / "pages").relative_to(out_dir))
    print("  bboxes  :", (out_dir / "bboxes").relative_to(out_dir))
    print("  overlay :", (out_dir / "overlays").relative_to(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
