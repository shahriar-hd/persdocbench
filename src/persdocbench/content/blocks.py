"""Structured document blocks: the generator asks for JSON, not free prose.

Why this exists
---------------
Two defects observed on 2026-11-20 in the plain-text generation path:

1. **Tables never arrived.** The prompt asked for ``|`` rows, but the provider
   returned prose, so a document that is *defined* by its table (invoice,
   form, medical record) rendered as paragraphs and the table was simply absent.
2. **Our own prompt scaffolding leaked into the page.** Asking for "بخشهای لازم:
   تیتر اصلی پوستر، زیرتیتر، ..." made the model echo the label as literal text,
   so a poster rendered the line "تیتر اصلی پوستر: تحلیل ..." on the page. The
   element name is *our* instruction, never document content.

The fix is structural: the provider returns a JSON object whose block ``role``
carries the element identity and whose ``text`` is content only. The renderer
places blocks by role; the preview prints content; the label is never a string
that can reach a page.

Layout/word-count compatibility is preserved because this module also *flattens*
the structure to text and markdown, so the gates, the preview and the renderer
keep working on records that have a flat ``text`` field.

Nothing here edits content: :func:`strip_role_labels` only removes a leading
echo of a *known element label we asked for*, and reports exactly what it
removed so the removal is auditable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from persdocbench.content.dispatcher import MalformedOutput

#: Block kinds the renderer understands. ``field`` is a label/value pair (forms),
#: ``table`` a real table, ``figure`` an image placeholder.
BLOCK_TYPES = (
    "title",
    "subtitle",
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "field",
)

#: Characters that may separate a leaked label from its value.
_LABEL_SEP = ":：؛,"


#: Raised when the provider answered but not with a usable document. Defined in
#: the dispatcher so it is part of the retry taxonomy (it subclasses ``Fatal`` and
#: is handled explicitly: retried, then fallen through, never a content rejection).
MalformedOutput = MalformedOutput


@dataclass
class Block:
    type: str
    text: str = ""
    role: str = ""
    items: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""
    label: str = ""
    value: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.role:
            d["role"] = self.role
        for key in ("text", "caption", "label", "value"):
            val = getattr(self, key)
            if val:
                d[key] = val
        if self.items:
            d["items"] = list(self.items)
        if self.header:
            d["header"] = list(self.header)
        if self.rows:
            d["rows"] = [list(r) for r in self.rows]
        return d


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in (_as_str(v) for v in value) if s]


def _as_rows(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    rows: list[list[str]] = []
    for raw in value:
        if isinstance(raw, list):
            cells = _as_str_list(raw)
            if cells:
                rows.append(cells)
        elif isinstance(raw, str) and raw.strip():
            rows.append(
                [c.strip() for c in raw.strip().strip("|").split("|") if c.strip()]
            )
    return rows


# An answer that uses a backslash the JSON grammar does not allow (``\%``, ``\_``,
# as Persian prose occasionally does) or a raw control character is still a perfectly
# readable object. Repairing those two syntax-only cases keeps the unit instead of
# losing it, and *marks* the repair so it can never look like a clean parse.
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def repair_json(text: str) -> str | None:
    """Neutralise syntax-only JSON defects; return ``None`` if none were found.

    Only two repairs are attempted, both of which leave every character of the
    provider's *text* in place:

    * a backslash the grammar does not allow becomes a literal backslash;
    * a raw control character inside a string becomes its ``\\u00XX`` escape.
    """
    repaired = _BAD_ESCAPE.sub(r"\\\\", text)
    repaired = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f]",
        lambda m: f"\\u{ord(m.group(0)):04x}",
        repaired,
    )
    return repaired if repaired != text else None


def extract_json(raw: str) -> dict[str, Any]:
    """Pull the JSON object out of a provider answer (payload only)."""
    return extract_json_ex(raw)[0]


def extract_json_ex(raw: str) -> tuple[dict[str, Any], bool]:
    """Pull the JSON object out of a provider answer, with a repair flag.

    Tolerates the shapes providers actually emit: bare JSON, a ```json fence, or a
    single sentence of prose before the object. Returns ``(payload, repaired)``;
    ``repaired`` is True only when the strict parse failed and :func:`repair_json`
    recovered the object, so a record can always say whether its structure arrived
    verbatim. Raises :class:`MalformedOutput` when no object can be recovered.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise MalformedOutput("empty answer")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    last: str | None = None
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last = exc.msg
            fixed = repair_json(candidate)
            if fixed is None:
                continue
            try:
                obj = json.loads(fixed)
            except json.JSONDecodeError:
                continue
            return _as_object(obj), True
        return _as_object(obj), False
    raise MalformedOutput(
        f"invalid JSON: {last or 'no JSON object in the answer'}"
    ) from None


def _as_object(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise MalformedOutput(f"top-level JSON is {type(obj).__name__}, expected object")
    return obj


def parse_blocks(payload: dict[str, Any]) -> list[Block]:
    """Convert a decoded payload into blocks, keeping only known block types."""
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        raise MalformedOutput("payload has no 'blocks' array")
    blocks: list[Block] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        btype = _as_str(raw.get("type")).lower()
        if btype not in BLOCK_TYPES:
            # Tolerate "paragraph " / "Table" / unknown kinds rather than lose text.
            text_only = " ".join(
                s
                for s in (_as_str(raw.get(k)) for k in ("text", "caption", "value"))
                if s
            )
            if text_only:
                blocks.append(
                    Block(type="paragraph", text=text_only, role=_as_str(raw.get("role")))
                )
            continue
        blocks.append(
            Block(
                type=btype,
                text=_as_str(raw.get("text")),
                role=_as_str(raw.get("role")),
                items=_as_str_list(raw.get("items")),
                header=_as_str_list(raw.get("header")),
                rows=_as_rows(raw.get("rows")),
                caption=_as_str(raw.get("caption")),
                label=_as_str(raw.get("label")),
                value=_as_str(raw.get("value")),
            )
        )
    if not blocks:
        raise MalformedOutput("no usable blocks")
    return blocks


def as_blocks(raw: Sequence[Block | dict[str, Any]]) -> list[Block]:
    """Accept either decoded :class:`Block` objects or their stored dict form.

    A record on disk holds ``blocks`` as plain dicts (JSONL), while the generator and
    tests work with ``Block`` objects. Making the flattener accept both keeps one
    implementation of the text/markdown conversion instead of a parallel copy that
    drifts (the renderer already reads dicts, the preview reads records).
    """
    out: list[Block] = []
    for item in raw:
        if isinstance(item, Block):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(
                Block(
                    type=_as_str(item.get("type")).lower() or "paragraph",
                    text=_as_str(item.get("text")),
                    role=_as_str(item.get("role")),
                    items=_as_str_list(item.get("items")),
                    header=_as_str_list(item.get("header")),
                    rows=_as_rows(item.get("rows")),
                    caption=_as_str(item.get("caption")),
                    label=_as_str(item.get("label")),
                    value=_as_str(item.get("value")),
                )
            )
    return out


def _label_variants(labels: dict[str, str], extra: list[str] | None = None) -> list[str]:
    names = set(labels.values()) | set(labels.keys())
    if extra:
        names |= set(extra)
    return sorted((n.strip() for n in names if n and n.strip()), key=len, reverse=True)


def strip_role_labels(
    text: str, labels: dict[str, str], extra: list[str] | None = None
) -> str:
    """Remove a leading echo of one of *our* element labels.

    Handles "تیتر اصلی پوستر: تحلیل ...", "متن بدنه:" (label with no value) and
    "تیتر اصلی پوستر — تحلیل ...". The label is prompt scaffolding, never
    content, so removing it is not an edit of the model's prose; callers record
    what was removed.
    """
    if not text:
        return text
    # Only the first line can carry a leaked label; never touch mid-text colons.
    head, sep, tail = text.partition("\n")
    for name in _label_variants(labels, extra):
        if head.startswith(name):
            rest = head[len(name) :]
            if rest and rest[0] in _LABEL_SEP:
                head = rest.lstrip("".join(_LABEL_SEP) + " \t")
                break
            if not rest.strip():
                head = ""
                break
    out = head + (sep + tail if sep else "")
    return out.strip()


#: Inline markdown the provider adds when formatting its *answer*. A document
#: page must show "۱۵۰ مگاوات", not "**۱۵۰ مگاوات**".
_INLINE_MARKUP = (
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),
    (re.compile(r"(?<!\w)__(.+?)__(?!\w)", re.S), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),
)


def strip_markup(text: str) -> str:
    """Remove inline markdown emphasis/code markers from a content string."""
    if not text:
        return text
    out = text
    for pattern, repl in _INLINE_MARKUP:
        out = pattern.sub(repl, out)
    return out


def flatten_prose(blocks: Sequence[Block | dict[str, Any]]) -> str:
    """Body prose only: paragraphs and list items. Headings, table rows and
    figure captions are excluded, matching the prompt's word-count definition."""
    blocks = as_blocks(blocks)
    parts: list[str] = []
    for b in blocks:
        if b.type == "paragraph" and b.text:
            parts.append(b.text)
        elif b.type == "list":
            parts.extend(b.items)
    return "\n\n".join(parts).strip()


def flatten_text(blocks: Sequence[Block | dict[str, Any]]) -> str:
    """The full document as plain text, for storage, sha256 and the legacy path."""
    blocks = as_blocks(blocks)
    parts: list[str] = []
    for b in blocks:
        if (b.type in ("title", "subtitle", "heading") and b.text) or (
            b.type == "paragraph" and b.text
        ):
            parts.append(b.text)
        elif b.type == "list" and b.items:
            parts.append("\n".join(f"- {i}" for i in b.items))
        elif b.type == "table":
            rows = []
            if b.header:
                rows.append("| " + " | ".join(b.header) + " |")
            rows.extend("| " + " | ".join(r) + " |" for r in b.rows)
            if b.caption:
                parts.append(b.caption)
            parts.append("\n".join(rows))
        elif b.type == "figure":
            parts.append(f"[FIG: {b.caption or b.text}]")
        elif b.type == "field" and (b.label or b.value):
            parts.append(f"{b.label}: {b.value}".strip(": "))
    return "\n\n".join(p for p in parts if p).strip()


def flatten_markdown(blocks: Sequence[Block | dict[str, Any]]) -> str:
    """Document as markdown with *real* tables, for previews and the renderer."""
    blocks = as_blocks(blocks)
    out: list[str] = []
    for b in blocks:
        if b.type == "title" and b.text:
            out.append(f"# {b.text}")
        elif b.type in ("subtitle", "heading") and b.text:
            out.append(f"## {b.text}")
        elif b.type == "paragraph" and b.text:
            out.append(b.text)
        elif b.type == "list" and b.items:
            out.append("\n".join(f"- {i}" for i in b.items))
        elif b.type == "table":
            if b.caption:
                out.append(b.caption)
            header = b.header or (b.rows[0] if b.rows else [])
            rows = b.rows if b.header else b.rows[1:]
            width = max([len(header), *(len(r) for r in rows)], default=0)
            if width:
                head = (header + [" "] * width)[:width]
                # One entry, single newlines: a markdown table only exists when the
                # header, the separator and the rows are adjacent lines. Blank lines
                # between them (the joining separator) render as loose paragraphs.
                lines = [
                    "| " + " | ".join(head) + " |",
                    "| " + " | ".join("---" for _ in range(width)) + " |",
                ]
                lines.extend(
                    "| " + " | ".join((r + [" "] * width)[:width]) + " |" for r in rows
                )
                out.append("\n".join(lines))
        elif b.type == "figure":
            out.append(f"[FIG: {b.caption or b.text}]")
        elif b.type == "field":
            out.append(f"{b.label}: {b.value}".strip(": "))
    return "\n\n".join(o for o in out if o).strip()


def decode(raw: str, labels: dict[str, str] | None = None) -> tuple[dict[str, Any], str]:
    """Full path: raw provider answer -> (record payload, text for the gates).

    Raises :class:`MalformedOutput` when the answer is not a usable document, so
    the caller can fall through to the next candidate model instead of recording
    a content rejection.
    """
    payload, repaired = extract_json_ex(raw)
    blocks = parse_blocks(payload)
    labels = labels or {}
    stripped: list[str] = []
    # Roles are structural ids and must be ASCII so the renderer can turn them into
    # usable CSS hooks. If the model answered with the Persian label instead, map it
    # back: the label is ours either way, and it must never reach the page.
    inverse = {v.strip(): k for k, v in labels.items() if v and v.strip()}
    inverse.update({k: k for k in labels})
    suspects: list[str] = []
    markup = 0
    for b in blocks:
        if b.role:
            b.role = inverse.get(b.role.strip(), b.role.strip())
        for attr in ("text", "caption", "value", "label", "title"):
            val = getattr(b, attr, "")
            if not val:
                continue
            unmarked = strip_markup(val)
            if unmarked != val:
                markup += 1
            new = strip_role_labels(unmarked, labels)
            if new != unmarked:
                stripped.append(unmarked[:60])
            elif any(unmarked.startswith(n) for n in _label_variants(labels)):
                # Starts with one of our labels but is a real sentence ("توالی
                # پنل‌های این صفحه ..."). Never edited: recorded so the leak is
                # measured instead of silently cleaned or silently shipped.
                suspects.append(unmarked[:60])
            setattr(b, attr, new)
        new_items: list[str] = []
        for item in b.items:
            unmarked = strip_markup(item)
            if unmarked != item:
                markup += 1
            cleaned = strip_role_labels(unmarked, labels)
            if cleaned != unmarked:
                stripped.append(unmarked[:60])
            new_items.append(cleaned)
        b.items = new_items
    title = strip_markup(_as_str(payload.get("title")))
    subtitle = strip_markup(_as_str(payload.get("subtitle")))
    if title and title != _as_str(payload.get("title")):
        markup += 1
    if subtitle and subtitle != _as_str(payload.get("subtitle")):
        markup += 1
    if title:
        blocks.insert(0, Block(type="title", text=strip_role_labels(title, labels)))
    if subtitle:
        blocks.insert(
            1 if title else 0,
            Block(type="subtitle", text=strip_role_labels(subtitle, labels)),
        )
    record = {
        "blocks": [b.to_dict() for b in blocks],
        "has_table": any(b.type == "table" and (b.header or b.rows) for b in blocks),
        "has_figure": any(b.type == "figure" for b in blocks),
        "label_echo_stripped": sorted(set(stripped)),
        "markup_fields_stripped": markup,
        "json_repaired": repaired,
        "label_prefix_suspect": sorted(set(suspects)),
    }
    return record, flatten_text(blocks)
