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
    assert "handwriting" in cfg["scope"]["deferred"]
    assert "formula" in cfg["scope"]["deferred"]
    assert cfg["annotation"]["coverage"]["entity_kie"] == 0


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
