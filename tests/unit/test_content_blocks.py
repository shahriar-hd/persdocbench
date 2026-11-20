"""D46: the provider answers with JSON blocks, not free prose.

The two defects these tests lock down (both observed on 2026-11-20):

* a table-native document (invoice/form/medical) rendered with **no table**,
  because "|" rows never came back in prose;
* the model echoed *our* element labels as literal page text, so a poster page
  read "تیتر اصلی پوستر: ...".
"""

from __future__ import annotations

import pytest

from persdocbench.content.blocks import (
    MalformedOutput,
    decode,
    extract_json,
    extract_json_ex,
    flatten_markdown,
    flatten_prose,
    flatten_text,
    parse_blocks,
    repair_json,
    strip_role_labels,
)

LABELS = {"headline": "تیتر اصلی پوستر", "subtitle": "زیرتیتر", "body": "متن بدنه"}


def test_extract_json_accepts_bare_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_unwraps_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_ignores_leading_prose():
    assert extract_json('البته! این سند است:\n{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("raw", ["", "   ", "no json here", "[1,2,3]", '{"a": '])
def test_extract_json_rejects_non_documents(raw):
    with pytest.raises(MalformedOutput):
        extract_json(raw)


def test_parse_blocks_requires_blocks_array():
    with pytest.raises(MalformedOutput):
        parse_blocks({"title": "x"})


def test_parse_blocks_keeps_unknown_kind_as_paragraph_text():
    blocks = parse_blocks({"blocks": [{"type": "callout", "text": "متن هشدار"}]})
    assert [b.type for b in blocks] == ["paragraph"]
    assert blocks[0].text == "متن هشدار"


def test_strip_role_labels_removes_leading_label_colon():
    assert (
        strip_role_labels("تیتر اصلی پوستر: تحلیل بار شبکه", LABELS) == "تحلیل بار شبکه"
    )


def test_strip_role_labels_drops_a_lone_label_line():
    assert strip_role_labels("متن بدنه:", LABELS) == ""


def test_strip_role_labels_never_touches_mid_text_colon():
    text = "بر پایهٔ گزارش: مصرف اوج ۱۵۰ مگاوات است"
    assert strip_role_labels(text, LABELS) == text


def test_strip_role_labels_keeps_a_sentence_starting_with_the_same_words():
    text = "تیتر اصلی پوستر باید کوتاه باشد"  # no separator -> content, not a label
    assert strip_role_labels(text, LABELS) == text


def test_decode_returns_blocks_and_a_figure_marker():
    raw = (
        '{"title": "گزارش بار", "blocks": ['
        '{"type": "paragraph", "role": "body", "text": "مصرف اوج ۱۵۰ مگاوات بود."},'
        '{"type": "figure", "role": "figure", "caption": "نمودار بار روزانه"},'
        '{"type": "table", "role": "table", "caption": "جدول ۱", '
        '"header": ["ساعت", "بار"], "rows": [["۱۸", "۱۵۰"]]}]}'
    )
    record, text = decode(raw, LABELS)
    assert record["has_table"] is True
    assert record["has_figure"] is True
    assert "[FIG: نمودار بار روزانه]" in text
    assert "| ساعت | بار |" in text


def test_decode_strips_an_echoed_label_and_reports_it():
    raw = (
        '{"blocks": [{"type": "paragraph", "role": "body", '
        '"text": "تیتر اصلی پوستر: تحلیل بار شبکه"}]}'
    )
    record, text = decode(raw, LABELS)
    assert "تیتر اصلی پوستر" not in text
    assert record["label_echo_stripped"] == ["تیتر اصلی پوستر: تحلیل بار شبکه"]


def test_flatten_prose_is_body_only():
    raw = (
        '{"blocks": ['
        '{"type": "heading", "text": "سرتیتر"},'
        '{"type": "paragraph", "text": "یک دو سه چهار"},'
        '{"type": "table", "header": ["a", "b"], "rows": [["c", "d"]]},'
        '{"type": "list", "items": ["پنج شش"]}]}'
    )
    blocks = parse_blocks(extract_json(raw))
    assert flatten_prose(blocks) == "یک دو سه چهار\n\nپنج شش"


def test_flatten_markdown_emits_a_real_table_with_a_separator_row():
    raw = (
        '{"blocks": [{"type": "table", "caption": "جدول ۱", "header": ["ساعت", "بار"], '
        '"rows": [["۱۸", "۱۵۰"], ["۱۹", "۱۴۲"]]}]}'
    )
    md = flatten_markdown(parse_blocks(extract_json(raw)))
    lines = md.splitlines()
    assert "| ساعت | بار |" in lines
    assert "| --- | --- |" in lines
    assert "| ۱۸ | ۱۵۰ |" in lines


def test_flatten_markdown_pads_ragged_rows_to_the_header_width():
    raw = '{"blocks": [{"type": "table", "header": ["a", "b"], "rows": [["1"]]}]}'
    md = flatten_markdown(parse_blocks(extract_json(raw)))
    assert "| 1 |   |" in md


def test_flatten_text_keeps_titles_and_field_pairs():
    raw = (
        '{"title": "فرم پذیرش", "blocks": ['
        '{"type": "field", "label": "نام", "value": "زهرا"}]}'
    )
    _record, text = decode(raw)
    assert "فرم پذیرش" in text
    assert "نام: زهرا" in text


def test_decode_never_raises_on_a_label_only_block():
    record, text = decode(
        '{"blocks": [{"type": "paragraph", "text": "متن بدنه:"}]}', LABELS
    )
    assert text == ""
    assert record["blocks"] == [{"type": "paragraph"}]


def test_strip_markup_removes_provider_emphasis_and_backticks():
    from persdocbench.content.blocks import strip_markup

    assert strip_markup("**۱۵۰ مگاوات**") == "۱۵۰ مگاوات"
    assert strip_markup("`bar نامی`") == "bar نامی"
    assert strip_markup("__مهم__") == "مهم"
    assert strip_markup("متن بی‌نشانه") == "متن بی‌نشانه"


def test_decode_reports_stripped_markup_counts():
    raw = '{"blocks": [{"type": "paragraph", "text": "**۱۵۰ مگاوات** بود."}]}'
    record, text = decode(raw, LABELS)
    assert text == "۱۵۰ مگاوات بود."
    assert record["markup_fields_stripped"] == 1


def test_decode_canonicalises_a_persian_role_back_to_its_id():
    raw = '{"blocks": [{"type": "paragraph", "role": "متن بدنه", "text": "متن"}]}'
    record, _ = decode(raw, LABELS)
    assert record["blocks"][0]["role"] == "body"


def test_decode_keeps_a_role_it_does_not_know():
    raw = '{"blocks": [{"type": "paragraph", "role": "masthead", "text": "متن"}]}'
    record, _ = decode(raw, LABELS)
    assert record["blocks"][0]["role"] == "masthead"


def test_decode_flags_a_label_prefixed_sentence_without_editing_it():
    """'توالی پنل‌های این صفحه ...' is a real sentence: keep it, report it."""
    raw = '{"blocks": [{"type": "paragraph", "role": "panel_flow", "text": "متن بدنه این گزارش را می‌خوانیم"}]}'
    record, text = decode(raw, LABELS)
    assert text == "متن بدنه این گزارش را می‌خوانیم"
    assert record["label_prefix_suspect"] == ["متن بدنه این گزارش را می‌خوانیم"]
    assert record["label_echo_stripped"] == []


def test_extract_json_repairs_a_backslash_the_grammar_forbids():
    """Persian prose occasionally emits \\% or \\_; the character is kept, the syntax fixed."""
    raw = '{"blocks": [{"type": "paragraph", "text": "نرخ رشد ۱۲\\% است"}]}'
    payload, repaired = extract_json_ex(raw)
    assert repaired is True
    assert payload["blocks"][0]["text"] == "نرخ رشد ۱۲\\% است"


def test_extract_json_reports_an_unrepaired_clean_parse():
    payload, repaired = extract_json_ex('{"a": 1}')
    assert (payload, repaired) == ({"a": 1}, False)


def test_repair_json_returns_none_when_there_is_nothing_to_repair():
    assert repair_json('{"a": "b"}') is None


def test_extract_json_repairs_a_raw_control_character():
    payload, repaired = extract_json_ex('{"a": "b\x01c"}')
    assert repaired is True
    assert payload["a"] == "b\x01c"


def test_decode_marks_a_repaired_answer():
    raw = '{"blocks": [{"type": "paragraph", "text": "نرخ ۱۲\\% رشد"}]}'
    record, text = decode(raw)
    assert record["json_repaired"] is True
    assert "۱۲" in text


def test_flatten_accepts_stored_dicts_as_well_as_block_objects():
    """A record on disk holds dicts; the preview must not need a second renderer."""
    stored = [
        {"type": "title", "text": "عنوان"},
        {"type": "table", "header": ["ردیف", "مبلغ"], "rows": [["۱", "۲۰۰"]]},
        {"type": "figure", "caption": "نمودار"},
    ]
    md = flatten_markdown(stored)
    assert md.startswith("# عنوان")
    assert "| ردیف | مبلغ |" in md and "| --- | --- |" in md
    assert "[FIG: نمودار]" in md and "[FIG: نمودار]" in flatten_text(stored)
    # a markdown table only exists if its lines are adjacent
    assert "| ردیف | مبلغ |\n| --- | --- |\n| ۱ | ۲۰۰ |" in md


def test_flatten_ignores_junk_entries_and_keeps_going():
    md = flatten_markdown([{"text": "بدون نوع"}, {"type": "paragraph", "text": "متن"}])
    assert "بدون نوع" in md and "متن" in md
