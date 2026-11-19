"""Unit tests for scripts/scan_local_fonts.py (pure functions, no fontconfig needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "scan_local_fonts.py"
_spec = importlib.util.spec_from_file_location("scan_local_fonts", SCRIPT)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)  # type: ignore[union-attr]


def test_parse_splits_commas_and_dedupes() -> None:
    out = scan.parse_fc_list_output("Sahel,Sahel Black\nVazirmatn\n\nVazirmatn\n")
    assert out == ["Sahel", "Sahel Black", "Vazirmatn"]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Sahel Black", "Sahel"),
        ("Vazirmatn ExtraBold", "Vazirmatn"),
        ("IRANSansXFaNum", "IRANSansX"),
        ("Amiri", "Amiri"),
        ("Noto Nastaliq Urdu", "Noto Nastaliq Urdu"),
    ],
)
def test_base_family_collapses_weights(name: str, expected: str) -> None:
    assert scan.base_family(name) == expected


def test_classify_prefers_inventory_then_licence_hints() -> None:
    inv = ["Vazirmatn", "Amiri"]
    assert scan.classify("Vazirmatn Light", inv) == "verified_inventory"
    assert scan.classify("IRANSansX", inv) == "proprietary_hint"
    assert scan.classify("DejaVu Sans", inv) == "free_hint"
    assert scan.classify("Some Random Face", inv) == "unknown"


def test_pending_families_are_not_reported_as_inventory() -> None:
    """Regression: IRANSansX once classified as verified because it sat in the file."""
    cfg = {
        "families": {
            "text_naskh": [
                {"name": "Vazirmatn", "licence": "OFL-1.1"},
                {
                    "name": "IranShahr",
                    "licence": "unverified",
                    "status": "excluded_pending_licence",
                },
            ]
        },
        "pending_licence_verification": [{"name": "IRANSansX"}],
    }
    verified, pending = scan.inventory_names(cfg)
    assert verified == ["Vazirmatn"]
    assert set(pending) == {"IRANSansX", "IranShahr"}


def test_report_shape_has_no_font_contents() -> None:
    """The report records names and paths, never font binaries or secrets."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "read_bytes" not in src
    assert "font_dirs" in src
