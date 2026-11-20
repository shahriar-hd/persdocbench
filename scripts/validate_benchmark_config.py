#!/usr/bin/env python3
"""Validate configs/benchmark/benchmark_v1.yaml against its own arithmetic.

The specification makes many count claims that must be mutually consistent
(domains sum to the core page budget, conditions sum to the image budget, splits
sum to the page budget, VQA categories sum to the item budget). A proposal with
internally inconsistent counts is not a spec, it is a wish. This script is the
guard.

Also emits docs/dataset/count_matrix_v1.csv (domain x provenance x condition x
task x difficulty), which is the Gate-2 count-matrix deliverable.

Usage:
    python scripts/validate_benchmark_config.py            # validate + write CSV
    python scripts/validate_benchmark_config.py --check    # validate only

Exit code 1 on any inconsistency.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("pyyaml required: uv pip install pyyaml")

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "benchmark" / "benchmark_v1.yaml"
OUT = ROOT / "docs" / "dataset" / "count_matrix_v1.csv"

SHARE_TOL = 1e-6
errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)


def close(a: float, b: float, tol: float = SHARE_TOL) -> bool:
    return abs(a - b) <= tol


def validate_register(cfg: dict) -> dict:
    """Check configs/content/register_v1.yaml against the benchmark profile.

    The register profile is a Gate-3 input, so a drift between it and the Gate-2
    domain set is a real failure: content generated for a domain that no longer
    exists (or a register mix that does not sum to 1) would silently bias the
    corpus. Cross-checks the two files rather than trusting either alone.
    """
    path = ROOT / "configs" / "content" / "register_v1.yaml"
    if not path.is_file():
        check(False, f"missing {path.relative_to(ROOT)}")
        return {"cells": 0, "calibration": "n/a"}

    reg = yaml.safe_load(path.read_text(encoding="utf-8"))
    domains = {d["id"] for d in cfg["domains"]}

    # -- axes sum to 1 ------------------------------------------------------
    sf_share = sum(v["share"] for v in reg["subject_fields"].values())
    check(close(sf_share, 1.0), f"subject_field shares sum {sf_share:.6f} != 1.0")
    r_share = sum(v["share"] for v in reg["registers"].values())
    check(close(r_share, 1.0), f"register shares sum {r_share:.6f} != 1.0")

    # -- lay_explainer is a defect, never a tier ----------------------------
    check(
        "lay_explainer" not in reg["registers"],
        "lay_explainer must not be a register tier",
    )

    # -- every domain has a register contract, and no orphan contract -------
    check(
        set(reg["domains"]) == domains,
        f"register domains {sorted(set(reg['domains']) - domains)} unknown / "
        f"{sorted(domains - set(reg['domains']))} missing a contract",
    )

    for did, spec in reg["domains"].items():
        mix = spec["register_mix"]
        check(
            close(sum(mix.values()), 1.0),
            f"{did} register_mix sums to {sum(mix.values()):.6f}, not 1.0",
        )
        for tier in mix:
            check(tier in reg["registers"], f"{did} uses unknown register {tier}")
        check(
            bool(spec.get("required_elements")),
            f"{did} declares no required structural elements",
        )

    # -- medical/biological content must have a home (user requirement) -----
    mb = reg["subject_fields"].get("medicine_biology")
    check(mb is not None, "medicine_biology subject_field is missing")
    if mb:
        check(
            mb["share"] >= 0.10,
            f"medicine_biology share {mb['share']} below the 10% floor",
        )
        check(
            len(mb["lexicon_seed"]) >= 10,
            "medicine_biology lexicon_seed too small for a density check",
        )
        check(
            set(mb["host_domains"]) <= domains, "medicine_biology hosts an unknown domain"
        )

    # -- banned patterns are the hard gate; an empty list disables it ------
    banned = reg["banned_patterns"]
    check(len(banned["fa"]) >= 10, "too few Persian banned patterns for a hard gate")
    check(len(banned["en"]) >= 4, "too few English banned patterns")

    # -- a hard gate must stay a hard gate ---------------------------------
    check(
        reg["checks"]["R2_banned_patterns"]["max"] == 0,
        "R2 banned-pattern max must be 0 (hard fail), not a graded score",
    )
    for tier, mn in reg["checks"]["R1_terminology_density"]["min"].items():
        check(tier in reg["registers"], f"R1 threshold for unknown register {tier}")
        check(mn > 0, f"R1 min density for {tier} must be positive")
    r5 = reg["checks"]["R5_complexity"]
    check(r5["min"] < r5["max"], "R5 min/max sentence length inverted")

    # -- calibration must not be claimed before it ran ----------------------
    cal = reg["calibration"]
    check(
        cal["status"] in {"not_started", "in_progress", "complete"},
        f"unknown calibration status {cal['status']}",
    )
    if cal["status"] != "complete":
        check(
            reg["approved"] is False,
            "register profile claims approval before calibration completed",
        )

    cells = len(domains) * len(reg["subject_fields"])
    return {"cells": cells, "calibration": cal["status"]}


def validate_fonts(cfg: dict) -> dict:
    """Cross-check configs/content/fonts_v1.yaml against the rendering requirements.

    D3/D30 asks for ~30 Persian families and handwriting-like styles. A requirement
    that lives only in prose is not a requirement, so the inventory is counted here
    and the declared counts in the inventory file are verified against its own lists.
    """
    path = ROOT / "configs" / "content" / "fonts_v1.yaml"
    if not path.is_file():
        check(False, f"missing {path.relative_to(ROOT)}")
        return {"families": 0, "handwriting_like": 0, "verified_on": "n/a"}
    fonts = yaml.safe_load(path.read_text(encoding="utf-8"))
    groups = fonts.get("families") or {}
    counts = fonts.get("counts") or {}
    render = cfg.get("rendering") or {}

    real: dict[str, int] = {g: len(rows or []) for g, rows in groups.items()}
    for group, n in real.items():
        check(
            counts.get(group) == n,
            f"fonts_v1.counts.{group} = {counts.get(group)} but the list has {n}",
        )
    total = sum(real.values())
    check(
        counts.get("total") == total,
        f"fonts_v1.counts.total = {counts.get('total')} but {total} families are listed",
    )

    verified = 0
    for _group, rows in groups.items():
        for entry in rows or []:
            licence = str(entry.get("licence", ""))
            status = str(entry.get("status", ""))
            if not licence or licence == "None":
                check(False, f"font {entry.get('id')} has no licence field")
            elif licence != "unverified" and "excluded" not in status:
                verified += 1
    check(
        counts.get("licence_verified") == verified,
        f"fonts_v1.counts.licence_verified = {counts.get('licence_verified')} "
        f"but {verified} entries carry a verified licence",
    )

    n_families = total
    hw = real.get("handwriting_like", 0)
    check(
        n_families >= render.get("families_min", 0),
        f"font families {n_families} < required {render.get('families_min')}",
    )
    check(
        hw >= render.get("handwriting_like_min", 0),
        f"handwriting-like families {hw} < required {render.get('handwriting_like_min')}",
    )
    check(
        render.get("nastaliq_page_share_cap")
        == fonts["policy"]["nastaliq_page_share_cap"],
        "rendering.nastaliq_page_share_cap != fonts_v1.policy.nastaliq_page_share_cap",
    )
    check(
        render.get("licence_verified_required") is True
        and fonts["policy"].get("output_images_carry_font_licence") is False,
        "font licence policy must require verification and disclaim output-image terms",
    )
    return {
        "families": n_families,
        "handwriting_like": hw,
        "verified_on": fonts.get("verified_on", "n/a"),
    }


def main(check_only: bool = False) -> int:
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    core = cfg["units"]["content_units_core"]
    images_total = cfg["units"]["images_total"]

    # ---- domains -----------------------------------------------------------
    domains = cfg["domains"]
    dsum = sum(d["pages"] for d in domains)
    check(dsum == core, f"domain pages {dsum} != content_units_core {core}")
    ssum = sum(d["share"] for d in domains)
    check(close(ssum, 1.0), f"domain shares sum {ssum:.6f} != 1.0")
    for d in domains:
        check(
            close(d["share"], d["pages"] / core, 1e-4),
            f"{d['id']}: share {d['share']} inconsistent with {d['pages']}/{core}",
        )
        ds = d["difficulty"]
        check(close(sum(ds.values()), 1.0), f"{d['id']}: difficulty shares != 1.0")

    grp = cfg["domain_groups"]
    grp_pages = sum(
        d["pages"] for d in domains if d["id"] in grp["administrative_cluster"]
    )
    check(
        close(grp_pages / core, grp["administrative_cluster_share_target"], 0.01),
        f"administrative cluster {grp_pages}/{core} = {grp_pages / core:.3f} "
        f"not within 0.01 of target {grp['administrative_cluster_share_target']}",
    )

    # ---- language / script / layout ---------------------------------------
    lm = cfg["language_mix"]
    check(
        sum(v["pages"] for v in lm.values()) == core,
        f"language_mix pages {sum(v['pages'] for v in lm.values())} != {core}",
    )
    check(close(sum(v["share"] for v in lm.values()), 1.0), "language_mix shares != 1.0")
    for k, v in lm.items():
        check(
            close(v["share"], v["pages"] / core, 1e-4),
            f"language_mix.{k}: share inconsistent with pages",
        )

    check(
        sum(cfg["script_style_mix"].values()) == core,
        f"script_style_mix sum {sum(cfg['script_style_mix'].values())} != {core}",
    )
    nast = cfg["script_style_mix"]["nastaliq"]
    check(
        nast / core <= 0.08 + 1e-9, f"nastaliq share {nast / core:.3f} exceeds the 8% cap"
    )

    fam = cfg["layout_families"]
    check(
        sum(f["pages"] for f in fam) == core,
        f"layout_families pages {sum(f['pages'] for f in fam)} != {core}",
    )

    # ---- page sizes: share of pages, not of images -------------------------
    check(close(sum(cfg["page_sizes"].values()), 1.0), "page_sizes shares != 1.0")

    # ---- conditions and image budget --------------------------------------
    cond = cfg["conditions"]
    pc_images = 0
    for code in ("P0", "P1a", "P1b"):
        c = cond[code]
        expected = c["pages_core"] * c.get("variants_per_page", 1)
        check(
            c["images"] == expected,
            f"{code}: images {c['images']} != pages*variants {expected}",
        )
        check(c["pages_core"] == core, f"{code}: pages_core {c['pages_core']} != {core}")
        pc_images += c["images"]

    p2 = cond["P2"]
    p2_images = sum(c["pages"] for c in p2["chains"])
    check(
        p2_images == p2["images"],
        f"P2: images {p2['images']} != sum of chain pages {p2_images}",
    )
    for c in p2["chains"]:
        check(
            c["pages"] <= p2["pages_core"],
            f"P2 chain {c['id']}: {c['pages']} pages exceeds the "
            f"{p2['pages_core']} captured pages",
        )
    check(
        any(c["pages"] == p2["pages_core"] for c in p2["chains"]),
        "P2: no chain captures the full page set",
    )
    p2_base, p2_rem = divmod(p2["pages_core"], len(domains))
    check(
        p2["domain_quota_per_domain"] == p2_base,
        f"P2: quota {p2['domain_quota_per_domain']} != floor("
        f"{p2['pages_core']}/{len(domains)}) = {p2_base}",
    )
    check(
        p2_base * len(domains) + p2_rem == p2["pages_core"],
        f"P2: {p2_base} x {len(domains)} + {p2_rem} != {p2['pages_core']}",
    )
    check(p2["pages_core"] <= core, "P2 pages exceed core page budget")
    sub = p2.get("subtracks") or {}
    check(
        sum(sub.values()) == p2["pages_core"],
        f"P2 subtracks {sum(sub.values())} != pages_core {p2['pages_core']}",
    )
    hw = sub.get("handwriting_executed", 0)
    check(
        hw == cfg["script_style_mix"].get("handwriting_executed"),
        "P2 handwritten pages != script_style_mix.handwriting_executed "
        f"({hw} vs {cfg['script_style_mix'].get('handwriting_executed')})",
    )
    check(
        hw > 0, "handwriting capture approved (D12) but no handwritten pages configured"
    )
    for c in p2["chains"]:
        check(
            c["pages"] >= len(domains),
            f"P2 chain {c['id']}: only {c['pages']} pages for {len(domains)} "
            "domains; the count matrix cannot give every domain an image",
        )

    parallel = cfg["parallel_control"]
    check(
        parallel["units"] * len(parallel["conditions"]) == parallel["images"],
        f"parallel_control: images {parallel['images']} != units*conditions",
    )
    check(
        parallel["units"] == cfg["units"]["content_units_parallel_control"],
        "parallel_control.units != units.content_units_parallel_control",
    )

    total_images = pc_images + p2["images"] + parallel["images"]
    check(
        total_images == images_total,
        f"image budget: {pc_images}+{p2['images']}+{parallel['images']} = "
        f"{total_images} != images_total {images_total}",
    )

    check(
        cfg["units"]["content_units_core"]
        + cfg["units"]["content_units_parallel_control"]
        == cfg["units"]["content_units_total"],
        "content unit totals do not add up",
    )

    # ---- splits -----------------------------------------------------------
    sp = cfg["splits"]
    ssum_pages = sum(
        sp[k]["content_units"] for k in ("dev", "test_public", "test_sealed")
    )
    check(ssum_pages == core, f"split pages {ssum_pages} != {core}")
    check(
        close(sum(sp[k]["share"] for k in ("dev", "test_public", "test_sealed")), 1.0),
        "split shares != 1.0",
    )
    pcsplit = sp["parallel_control"]
    check(
        sum(pcsplit.values()) == parallel["units"],
        f"parallel_control split {sum(pcsplit.values())} != {parallel['units']}",
    )

    # ---- OOD strata -------------------------------------------------------
    check(close(sum(cfg["ood_strata"].values()), 1.0), "ood_strata shares != 1.0")

    # ---- VQA --------------------------------------------------------------
    vqa = cfg["vqa"]
    check(
        sum(vqa["categories"].values()) == vqa["total"],
        f"vqa categories {sum(vqa['categories'].values())} != total {vqa['total']}",
    )
    check(vqa["total"] == cfg["units"]["qa_items"], "vqa.total != units.qa_items")

    # ---- annotation coverage sanity ---------------------------------------
    ann = cfg["annotation"]
    for k, v in ann["human_verification_rate"].items():
        check(0.0 < v <= 1.0, f"human_verification_rate.{k} out of range: {v}")
    check(ann["coverage"]["entity_kie"] == 0, "entity_kie must be deferred in v1")

    # ---- annotation effort is computed, not asserted ----------------------
    hours = sum(e["count"] * e["seconds_each"] for e in ann["effort"]) / 3600.0
    check(
        abs(hours - ann["effort_hours_estimate"]) <= 1.0,
        f"annotation effort {hours:.1f} h != declared {ann['effort_hours_estimate']} h",
    )
    images_for_pass = sum(cond[c]["images"] for c in ("P0", "P1a", "P1b")) + p2["images"]
    check(
        ann["page_pass"]["images"] == images_for_pass,
        f"page_pass images {ann['page_pass']['images']} != render+P2 images "
        f"{images_for_pass}",
    )
    check(
        ann["page_pass"]["seconds_per_image"] * ann["page_pass"]["images"] / 3600.0
        <= hours,
        "page pass alone exceeds the declared effort budget",
    )
    hw_pages = p2["subtracks"]["handwriting_executed"]
    check(
        any(
            e["task"] == "handwriting_fidelity" and e["count"] == hw_pages
            for e in ann["effort"]
        ),
        "no handwriting fidelity check budgeted for the handwritten pages",
    )

    # ---- sealed test (D6) and licence (D33) -------------------------------
    sealed = cfg.get("sealed_test") or {}
    check(
        sealed.get("images_published") is False
        and sealed.get("text_gt_published") is False,
        "sealed_test must withhold images AND text: labels are the leak",
    )
    check(
        sealed.get("scoring_budget_per_model")
        == cfg["splits"]["leakage_controls"]["sealed_scoring_budget"],
        "sealed_test.scoring_budget_per_model != splits.leakage_controls."
        "sealed_scoring_budget",
    )
    rel = cfg["release"]
    check(
        rel["dataset_licence_proposed"]
        in ("CC-BY-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC0-1.0"),
        f"unknown dataset licence {rel['dataset_licence_proposed']!r}",
    )
    check(
        rel.get("commercial_licence") in ("on_request", "granted", "not_offered"),
        "release.commercial_licence must state the dual-licensing position",
    )

    # ---- font inventory (D3/D30, cross-config) ---------------------------
    font_stats = validate_fonts(cfg)

    # ---- content register (cross-config consistency) ----------------------
    reg_stats = validate_register(cfg)

    # ---- report -----------------------------------------------------------
    if errors:
        print(f"FAIL — {len(errors)} inconsistency/inconsistencies:")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print("OK — all count claims are internally consistent")
    print(
        f"  content units : {cfg['units']['content_units_total']} "
        f"({core} core + {cfg['units']['content_units_parallel_control']} parallel)"
    )
    print(f"  images        : {total_images}")
    print(f"  vqa items     : {vqa['total']}")
    print(
        f"  annotation    : ~{ann['effort_hours_estimate']} h "
        f"(computed {hours:.1f} h, +{ann.get('effort_hours_second_annotator', 0)} h "
        f"second annotator)"
    )
    print(
        f"  fonts         : {font_stats['families']} families "
        f"({font_stats['handwriting_like']} handwriting-like), "
        f"licences verified {font_stats['verified_on']}"
    )
    print(
        f"  register cells: {reg_stats['cells']} (domain x subject_field), "
        f"calibration: {reg_stats['calibration']}"
    )

    if check_only:
        return 0

    # count matrix: domain x condition x capability, pages and images
    caps = cfg["scope"]["capabilities"]
    rows = []
    # P2 chain pages are distributed over domains with a deterministic remainder
    # (first `rem` domains by id get base+1), so an odd chain size no longer
    # misallocates images: the per-domain image sum still equals sum(chain pages).
    p2_alloc = {d["id"]: 0 for d in domains}
    for c in p2["chains"]:
        per = c["pages"] // len(domains)
        rem = c["pages"] % len(domains)
        for i, did in enumerate(sorted(p2_alloc)):
            p2_alloc[did] += per + (1 if i < rem else 0)
    for i, d in enumerate(sorted(domains, key=lambda d: d["id"])):
        d_pages = d["pages"]
        d_p2 = p2_base + (1 if i < p2_rem else 0)
        for code in ("P0", "P1a", "P1b", "P2"):
            if code == "P2":
                pages = d_p2
                p2_img = p2_alloc[d["id"]]
                mult = p2_img / d_p2
            else:
                pages = d_pages
                mult = 1
            for cap in caps:
                # table capability only applies to pages containing tables (est. share)
                rows.append(
                    {
                        "domain": d["id"],
                        "condition": code,
                        "capability": cap,
                        "content_units": pages,
                        "images": round(pages * mult),
                        "difficulty_easy": round(pages * d["difficulty"]["easy"]),
                        "difficulty_medium": round(pages * d["difficulty"]["medium"]),
                        "difficulty_hard": round(pages * d["difficulty"]["hard"]),
                        "content_origin": "synthetic_native",
                        "acquisition_path": cond[code]["acquisition_path"],
                        "in_headline": "yes",
                    }
                )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {OUT.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check",
        action="store_true",
        help="validate only, do not write the count matrix",
    )
    args = ap.parse_args()
    sys.exit(main(check_only=args.check))
