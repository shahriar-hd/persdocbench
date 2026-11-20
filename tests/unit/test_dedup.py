"""Near-duplicate audit (D46 companion): MinHash + exact confirmation."""

from __future__ import annotations

import json

import pytest

from persdocbench.content.dedup import (
    DEFAULT_THRESHOLD,
    UniquenessReport,
    as_markdown,
    audit,
    exact_jaccard,
    jaccard,
    minhash,
    shingles,
)

BODY = (
    "در این گزارش چارچوب ارزیابی ریسک اعتباری برای پرتفوی بانکی بررسی می‌شود. "
    "داده‌های ترازنامه‌ای سه سال گذشته با مدل رگرسیون پنل تحلیل شده‌اند و "
    "نتیجه نشان می‌دهد نسبت ذخیره مطالبات به مانده تسهیلات در دورهٔ رکود "
    "افزایش می‌یابد. توصیهٔ عملیاتی، بازنگری سالانهٔ سقف بدهکار است."
)


def test_shingles_are_folded_and_length_bounded():
    assert shingles(BODY, k=5)  # long enough
    assert shingles("یعنی", k=5) == set()  # no shingle evidence at all
    # Arabic yeh/kaf and harakat fold onto their Persian forms, so the same sentence
    # cannot look unique through a keyboard difference.
    assert shingles("كتابي", k=1) == shingles("کتابی", k=1)
    assert shingles("مُدل", k=1) == shingles("مدل", k=1)


def test_k_must_be_positive():
    with pytest.raises(ValueError):
        shingles(BODY, k=0)


def test_minhash_is_deterministic_and_rejects_empty():
    a = minhash(BODY, permutations=32, seed=7)
    b = minhash(BODY, permutations=32, seed=7)
    assert a == b and len(a) == 32
    assert minhash("یعنی", permutations=32) == ()
    assert minhash(BODY, permutations=32, seed=8) != a


def test_jaccard_exact_and_estimated_agree_on_identity():
    sig = minhash(BODY, permutations=128)
    assert jaccard(sig, sig) == 1.0
    assert jaccard(sig, ()) == 0.0
    assert jaccard(sig, sig[:10]) == 0.0  # length mismatch is not a similarity


def test_exact_jaccard_bounds():
    a = shingles(BODY)
    assert exact_jaccard(a, a) == 1.0
    assert exact_jaccard(a, set()) == 0.0
    one_word = exact_jaccard(a, shingles(BODY.replace("افزایش می‌یابد", "کاهش می‌یابد")))
    two_words = exact_jaccard(
        a,
        shingles(
            BODY.replace("ذخیره مطالبات", "سرمایه گذاری").replace("بانکی", "بیمه‌ای")
        ),
    )
    assert 0.5 < one_word < 1.0
    assert two_words < one_word  # one more edit means fewer shared shingles
    assert exact_jaccard(a, shingles("یک متن کاملاً بی‌ربط دربارهٔ آشپزی و گیاهان")) < 0.05


def test_audit_groups_a_repeat_within_a_cell():
    records = [
        {
            "unit_id": "a",
            "domain": "news_web",
            "subject_field": "finance_economics",
            "register": "expert_to_expert",
            "text": BODY,
        },
        {
            "unit_id": "b",
            "domain": "news_web",
            "subject_field": "finance_economics",
            "register": "expert_to_expert",
            "text": BODY,
        },
        {
            "unit_id": "c",
            "domain": "news_web",
            "subject_field": "finance_economics",
            "register": "expert_to_expert",
            "text": BODY.replace("رکود", "تورم").replace("سالانه", "فصلی"),
        },
        {
            "unit_id": "d",
            "domain": "news_web",
            "subject_field": "finance_economics",
            "register": "public_facing_formal",
            "text": BODY,
        },
    ]
    report = audit(records, threshold=0.85, permutations=128)
    assert isinstance(report, UniquenessReport)
    # 'a' and 'b' are byte-identical: one keeper ('a'), one duplicate ('b').
    ids = {u for c in report.clusters for u, _ in c.duplicates}
    assert ids == {"b"}
    assert report.n_duplicate_records == 1
    assert report.n_unique_records == 3
    assert report.clusters[0].keeper == "a"

    # 'c' differs by two words (~0.7 exact Jaccard) and is unique at 0.85, not at 0.6;
    # 'd' is byte-identical but a different register cell: not a defect by default.
    assert "c" not in ids
    assert "c" in {
        u for cl in audit(records, threshold=0.6).clusters for u, _ in cl.duplicates
    }
    # 'd' is identical but a different register cell: not a defect by default.
    assert report.clusters[0].cell == (
        "news_web",
        "finance_economics",
        "expert_to_expert",
    )


def test_audit_without_cell_isolation_sees_the_cross_cell_repeat():
    records = [
        {
            "unit_id": "a",
            "domain": "poster",
            "subject_field": "medicine_biology",
            "register": "public_facing_formal",
            "text": BODY,
        },
        {
            "unit_id": "b",
            "domain": "administrative_letter",
            "subject_field": "medicine_biology",
            "register": "expert_to_expert",
            "text": BODY,
        },
    ]
    assert audit(records, threshold=0.85, within_cell_only=True).n_duplicate_records == 0
    assert audit(records, threshold=0.85, within_cell_only=False).n_duplicate_records == 1


def test_audit_counts_short_texts_separately_and_never_calls_them_duplicates():
    records = [
        {
            "unit_id": "a",
            "domain": "x",
            "subject_field": "y",
            "register": "z",
            "text": "کوتاه",
        },
        {
            "unit_id": "b",
            "domain": "x",
            "subject_field": "y",
            "register": "z",
            "text": "کوتاه",
        },
    ]
    report = audit(records, threshold=0.85)
    assert report.n_without_shingles == 2
    assert report.n_duplicate_records == 0
    assert report.duplicate_rate == 0.0


def test_empty_report_serialises_and_renders():
    report = UniquenessReport()
    assert report.duplicate_rate == 0.0
    assert json.loads(json.dumps(report.as_dict()))["n_records"] == 0
    assert "records audited | 0" in as_markdown(report)


def test_markdown_lists_clusters():
    records = [
        {
            "unit_id": "a",
            "domain": "d",
            "subject_field": "s",
            "register": "r",
            "text": BODY,
        },
        {
            "unit_id": "b",
            "domain": "d",
            "subject_field": "s",
            "register": "r",
            "text": BODY,
        },
    ]
    md = as_markdown(audit(records))
    assert "near-duplicate | 1" in md
    assert "| a | b | 1.000 |" in md


def test_default_threshold_matches_config():
    """configs/content/generation_v1.yaml: minhash_jaccard_reject: 0.85."""
    assert DEFAULT_THRESHOLD == 0.85


def test_report_exposes_nearest_neighbour_similarity_not_only_a_verdict():
    """'No duplicates at 0.85' is weak; the closest pair is the measurable claim."""
    near = BODY.replace("رکود", "تورم")
    records = [
        {
            "unit_id": "a",
            "domain": "d",
            "subject_field": "s",
            "register": "r",
            "text": BODY,
        },
        {
            "unit_id": "b",
            "domain": "d",
            "subject_field": "s",
            "register": "r",
            "text": near,
        },
    ]
    report = audit(records, threshold=0.95)
    assert report.n_duplicate_records == 0  # nothing crosses 0.95
    assert report.nearest == [0.0, pytest.approx(report.top_pairs[0][0])]
    quantiles = report.nearest_quantiles()
    assert 0.0 < quantiles["max"] < 0.95
    data = report.as_dict()
    assert data["closest_pairs"][0]["similarity"] == quantiles["max"]
    assert data["closest_pairs"][0]["keeper"] == "a"
    assert data["closest_pairs"][0]["duplicate"] == "b"
    assert "max (closest pair in the run)" in as_markdown(report)
