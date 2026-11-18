"""Contract tests for the benchmark specification.

The Gate-2 spec states many counts that must be mutually consistent. These tests
fail the build if the proposal drifts out of internal agreement — the cheapest
possible defence against publishing a benchmark whose own arithmetic is wrong.

The validator script is the single implementation; tests exercise it rather than
duplicating the arithmetic.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = ROOT / "configs" / "benchmark" / "benchmark_v1.yaml"
ONTOLOGY_PATH = ROOT / "configs" / "annotation" / "ontology_v1.yaml"
VALIDATOR = ROOT / "scripts" / "validate_benchmark_config.py"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ontology() -> dict:
    return yaml.safe_load(ONTOLOGY_PATH.read_text(encoding="utf-8"))


def test_config_files_exist() -> None:
    assert CFG_PATH.is_file(), f"missing {CFG_PATH}"
    assert ONTOLOGY_PATH.is_file(), f"missing {ONTOLOGY_PATH}"


def test_validator_passes() -> None:
    """All count claims are internally consistent (domains, conditions, splits, VQA)."""
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"validator failed:\n{proc.stdout}\n{proc.stderr}"
    assert "OK" in proc.stdout


def test_status_is_draft_until_approved(cfg: dict) -> None:
    """A config claiming approval before Gate 2 sign-off is a process bug."""
    assert cfg["status"] == "draft"
    assert cfg["approved"] is False


def test_paired_conditions_are_mandatory(cfg: dict) -> None:
    """Constraint C2: every core page exists in >= 2 conditions with one GT."""
    conds = cfg["conditions"]
    for code in ("P0", "P1a", "P1b"):
        assert conds[code]["pages_core"] == cfg["units"]["content_units_core"]
    assert len(conds["P2"]["chains"]) >= 2


def test_provenance_never_claims_real_documents(cfg: dict) -> None:
    """Content origin is synthetic in v1; capture path may be physical."""
    assert cfg["provenance"]["content_origin"] == {"synthetic_native": 1.0}
    assert "print_capture" in cfg["provenance"]["acquisition_paths"]
    assert cfg["provenance"]["third_party_content"] == "none"


def test_banking_insurance_and_financial_are_not_renamed(cfg: dict) -> None:
    """Domain ids feed file naming and split manifests; renaming breaks traceability."""
    ids = {d["id"] for d in cfg["domains"]}
    assert "banking_insurance" in ids
    assert "financial_invoice" in ids
    assert len(ids) == 10


def test_deferred_capabilities_stay_out_of_v1(cfg: dict) -> None:
    assert "formula" in cfg["scope"]["deferred"]
    assert cfg["annotation"]["coverage"]["entity_kie"] == 0


def test_handwriting_is_a_capture_mode_not_a_source(cfg: dict) -> None:
    """D12: humans copy our text by hand. It must never be described as real documents."""
    assert "handwriting" in cfg["scope"]["script_styles"]
    assert "handwriting_capture" in cfg["scope"]["capture_modes"]
    assert cfg["scope"]["handwriting_scope"] == "capture_mode_only"
    hw = cfg["handwriting_capture"]
    assert hw["page_source"] == "rendered_template"
    assert hw["writers_min"] >= 8
    assert hw["never_describe_as"] == "real_documents"


def test_p2_is_300_pages_with_a_handwritten_subtrack(cfg: dict) -> None:
    """D4/D12: 300 physical pages, split 180 printed / 120 hand-executed."""
    p2 = cfg["conditions"]["P2"]
    assert p2["pages_core"] == 300
    assert sum(c["pages"] for c in p2["chains"]) == p2["images"] == 400
    assert p2["subtracks"] == {"printed_capture": 180, "handwriting_executed": 120}
    assert p2["domain_quota_per_domain"] * 10 == p2["pages_core"]


def test_every_page_gets_a_human_pass_but_box_gt_stays_sampled(cfg: dict) -> None:
    """D32: 100 % page pass at 20 s/page, explicitly not word-level verification."""
    ann = cfg["annotation"]
    rates = ann["human_verification_rate"]
    for key in ("layout", "text", "reading_order", "markdown", "tables", "vqa"):
        assert rates[key] == 1.00, f"{key} no longer fully reviewed"
    assert rates["line_word_boxes"] < 1.0
    assert ann["page_pass"]["seconds_per_image"] == 20
    assert ann["page_pass"]["images"] == 4900


def test_annotation_effort_is_arithmetic_not_assertion(cfg: dict) -> None:
    """The 251 h figure must be reproducible from the table it is printed with."""
    ann = cfg["annotation"]
    hours = sum(e["count"] * e["seconds_each"] for e in ann["effort"]) / 3600.0
    assert abs(hours - ann["effort_hours_estimate"]) <= 1.0
    assert ann["effort_hours_estimate"] > 229, "100 % page pass must raise the budget"


def test_sealed_split_withholds_images_and_labels(cfg: dict) -> None:
    """D6: a downloadable test split measures memorisation as much as capability."""
    sealed = cfg["sealed_test"]
    assert sealed["images_published"] is False
    assert sealed["text_gt_published"] is False
    assert sealed["submission_format"] == "predictions_json"
    assert sealed["scoring_budget_per_model"] == \
        cfg["splits"]["leakage_controls"]["sealed_scoring_budget"]


def test_dataset_licence_choice_is_recorded_with_its_cost(cfg: dict) -> None:
    """D33: non-commercial is a choice with a price; the price is written down."""
    rel = cfg["release"]
    assert rel["dataset_licence_proposed"] == "CC-BY-NC-4.0"
    assert rel["commercial_licence"] == "on_request"
    assert "industry" in rel["licence_rationale"]
    assert rel["code_licence"] == "Apache-2.0"
    assert rel["redistribute_third_party"] is False


def test_ontology_has_persian_specific_classes(ontology: dict) -> None:
    """P4 (approved): marginal notes and stamps/signatures are first-class."""
    classes = ontology["layout_classes"]
    for cls in ("marginal_note", "stamp", "signature"):
        assert cls in classes, f"missing approved layout class {cls}"
        assert classes[cls]["persian_specific"] is True


def test_ontology_keeps_text_fields_separate(ontology: dict) -> None:
    """Approved protocol P1: raw and normalised text are distinct fields."""
    fields = ontology["text_fields"]
    assert "text_raw" in fields
    assert "text_norm" in fields
    assert "zwnj_removal" in fields["text_norm"]["excludes"]
    assert "digit_folding" in fields["text_norm"]["excludes"]


def test_table_model_requires_spans(ontology: dict) -> None:
    """The legacy pipeline had zero rowspan/colspan support; this must not recur."""
    cells = ontology["table_model"]["fields"]
    assert any("cells" in str(c) for c in cells)
    joined = " ".join(ontology["table_model"]["rules"])
    assert "rowspan" in joined and "colspan" in joined


def test_count_matrix_matches_config() -> None:
    """The generated matrix must cover exactly the configured core pages per condition."""
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    out = ROOT / "docs" / "dataset" / "count_matrix_v1.csv"
    if not out.is_file():
        pytest.skip("count matrix not generated; run scripts/validate_benchmark_config.py")
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows, "empty count matrix"

    core = cfg["units"]["content_units_core"]
    per_condition: dict[str, int] = {}
    for r in rows:
        per_condition.setdefault(r["condition"], 0)
    for cond in ("P0", "P1a", "P1b"):
        pages = sum(
            int(r["content_units"]) for r in rows
            if r["condition"] == cond and r["capability"] == "page_ocr"
        )
        assert pages == core, f"{cond}: matrix covers {pages} pages, config says {core}"
    p2 = cfg["conditions"]["P2"]
    p2_units = sum(
        int(r["content_units"]) for r in rows
        if r["condition"] == "P2" and r["capability"] == "page_ocr"
    )
    assert p2_units == p2["pages_core"], f"P2 matrix covers {p2_units} pages"
    p2_images = sum(
        int(r["images"]) for r in rows
        if r["condition"] == "P2" and r["capability"] == "page_ocr"
    )
    assert p2_images == p2["images"], f"P2 matrix covers {p2_images} images"


def test_font_inventory_meets_the_30_family_requirement(cfg: dict) -> None:
    """D3/D30: ~30 Persian families, >= 4 handwriting-like, every licence verified."""
    fonts = yaml.safe_load((ROOT / "configs" / "content" / "fonts_v1.yaml")
                           .read_text(encoding="utf-8"))
    groups = fonts["families"]
    total = sum(len(v) for v in groups.values())
    assert total >= cfg["rendering"]["families_min"] >= 30
    assert len(groups["handwriting_like"]) >= cfg["rendering"]["handwriting_like_min"] >= 4
    assert fonts["counts"]["total"] == total
    unverified = [e["id"] for rows in groups.values() for e in rows
                  if e.get("licence") == "unverified"]
    assert unverified, "the unverified entry must stay visible, not deleted"
    assert all(e.get("licence_sha") or e.get("repo") for rows in groups.values()
               for e in rows), "every family must name its source or its licence sha"


def test_proprietary_fonts_are_excluded_not_silently_used(cfg: dict) -> None:
    """B Nazanin and IRANSansX are the most common Persian fonts and both are blocked."""
    fonts = yaml.safe_load((ROOT / "configs" / "content" / "fonts_v1.yaml")
                           .read_text(encoding="utf-8"))
    pending = " ".join(str(e) for e in fonts["pending_licence_verification"])
    assert "IRANSansX" in pending
    assert "Nazanin" in pending
    assert fonts["policy"]["redistribute_font_files"] is False
    assert fonts["policy"]["output_images_carry_font_licence"] is False

# --- content register profile (Gate-3 input) --------------------------------

REGISTER_PATH = ROOT / "configs" / "content" / "register_v1.yaml"


@pytest.fixture(scope="module")
def register() -> dict:
    return yaml.safe_load(REGISTER_PATH.read_text(encoding="utf-8"))


def test_register_profile_exists(register: dict) -> None:
    assert register["status"] == "draft"
    assert register["calibration"]["status"] == "not_started"


def test_axes_sum_to_one(register: dict) -> None:
    """Shares are sampling weights; a non-normalised axis silently skews the corpus."""
    sf = sum(v["share"] for v in register["subject_fields"].values())
    rg = sum(v["share"] for v in register["registers"].values())
    assert abs(sf - 1.0) < 1e-9, f"subject_field shares sum to {sf}"
    assert abs(rg - 1.0) < 1e-9, f"register shares sum to {rg}"


def test_lay_explainer_is_a_defect_not_a_tier(register: dict) -> None:
    """The user's reported failure mode: magazine prose for a specialist audience."""
    assert "lay_explainer" not in register["registers"]
    assert register["checks"]["R2_banned_patterns"]["max"] == 0


def test_specialist_register_dominates(register: dict) -> None:
    """expert_to_expert must be the largest tier, not merely present."""
    shares = {k: v["share"] for k, v in register["registers"].items()}
    assert shares["expert_to_expert"] == max(shares.values())
    assert shares["expert_to_expert"] >= 0.50
    assert shares["public_facing_formal"] <= 0.05


def test_medical_content_has_a_home(register: dict, cfg: dict) -> None:
    """Medicine/biology is a subject_field, not an 11th domain (D13/D18)."""
    mb = register["subject_fields"]["medicine_biology"]
    assert mb["share"] >= 0.10
    assert len(mb["lexicon_seed"]) >= 10
    domain_ids = {d["id"] for d in cfg["domains"]}
    assert set(mb["host_domains"]) <= domain_ids
    assert "medicine_biology" not in domain_ids


def test_every_domain_has_a_register_contract(register: dict, cfg: dict) -> None:
    domain_ids = {d["id"] for d in cfg["domains"]}
    assert set(register["domains"]) == domain_ids
    for did, spec in register["domains"].items():
        assert abs(sum(spec["register_mix"].values()) - 1.0) < 1e-9, did
        assert spec["required_elements"], f"{did} has no required structure"
        for tier in spec["register_mix"]:
            assert tier in register["registers"], f"{did}: unknown tier {tier}"


def test_banned_patterns_cannot_be_empty(register: dict) -> None:
    banned = register["banned_patterns"]
    assert len(banned["fa"]) >= 10
    assert len(banned["en"]) >= 4
    # The exact phrasing from the reported failure mode must be caught.
    joined = " ".join(banned["fa"])
    assert "به زبان ساده" in joined
