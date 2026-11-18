"""Normalisation and content gates for generated Persian text.

Two different jobs live here and they must not be confused:

1. **Storage normalisation** — mechanical, lossless-in-spirit: Unicode NFC, line
   ending unification, removal of stray control characters and collapse of
   repeated spaces. It never removes ZWNJ, never folds ۰-۹ into 0-9, never
   substitutes ی/ي or ک/ك. Those distinctions are content, and the benchmark's
   whole point is that they are measured, not destroyed (metric protocol P1).

2. **Content gates** — deterministic checks that decide whether generated text is
   acceptable at all: banned lay-explainer patterns (hard fail), terminology
   density, numeric density, sentence-length distribution, and assistant-echo
   detection. These produce *numbers and verdicts*, never edits.

A gate that silently rewrites text would make the benchmark a measurement of our
cleanup script instead of the model's output.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

ZWNJ = "\u200c"
ZWNJ_TAIL = "\u200d"
KASHIDA = "\u0640"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ARABIC_TO_PERSIAN = str.maketrans(ARABIC_DIGITS, PERSIAN_DIGITS)

CONTROL_CHARS = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")
MULTISPACE = re.compile(r"[ \t]{2,}")
MANY_NEWLINES = re.compile(r"\n{3,}")
SENTENCE_SPLIT = re.compile(r"[.!?؟…\n]+")
WORD = re.compile(r"[^\s.،؛:!?؟…()\[\]{}\"'«»]+")
NUMBER_WITH_UNIT = re.compile(
    r"[0-9۰-۹٠-٩][0-9۰-۹٠-٩.,٫]*\s*"
    r"(?:%|٪|درصد|ریال|تومان|هزار|میلیون|میلیارد|کیلوگرم|کیلو|گرم|میلی‌گرم|"
    r"میلی‌لیتر|لیتر|متر|سانتی‌متر|میلی‌متر|کیلومتر|سانتی‌گراد|درجه|واحد|"
    r"نفر|مورد|صفحه|ساعت|دقیقه|ثانیه|روز|ماه|سال|بار|دوز|میلی‌گرم|عدد|جلد|نسخه)"
)


# ---------------------------------------------------------------------------
# Storage normalisation
# ---------------------------------------------------------------------------
def normalize_for_storage(text: str) -> str:
    """Lossless-ish cleanup applied before a generated text is stored.

    Preserves ZWNJ, tatweel, diacritics, Persian/Arabic-Indic digit sets and
    Arabic-vs-Persian letter choices. Removes only what is unambiguously noise.
    """
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text)!r}")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    text = text.replace(ZWNJ_TAIL, "")
    text = CONTROL_CHARS.sub("", text)
    text = "\n".join(MULTISPACE.sub(" ", ln).strip() for ln in text.split("\n"))
    text = MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Feature extraction (measurement, not modification)
# ---------------------------------------------------------------------------
def words(text: str) -> list[str]:
    return WORD.findall(text)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def fold_for_matching(text: str) -> str:
    """Case/diacritic/ZWNJ-insensitive form used ONLY for pattern matching.

    Never used for storage or scoring: it exists so that ``همانطور که میدانید``
    is matched whether or not the generator inserted a ZWNJ or a half-space.
    ZWNJ is *deleted*, not turned into a space, so ``می‌رود`` and ``میرود`` fold
    to the same string; callers that also need space-insensitivity use
    :func:`squash`.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace(ZWNJ, "").replace(ZWNJ_TAIL, "").replace(KASHIDA, "")
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)  # harakat/tanwin
    text = text.translate(ARABIC_TO_PERSIAN)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("\u0623", "ا").replace("\u0625", "ا")
    return re.sub(r"\s+", " ", text).strip().lower()


def squash(text: str) -> str:
    """Folded text with all whitespace removed — for ZWNJ/space-tolerant matching."""
    return re.sub(r"\s+", "", fold_for_matching(text))


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    """Match a phrase allowing any mix of spaces/ZWNJ between its words."""
    words = [w for w in fold_for_matching(phrase).split(" ") if w]
    if not words:
        return re.compile(r"(?!x)x")  # never matches
    return re.compile(r"[\s]*".join(re.escape(w) for w in words))


def terminology_density(text: str, lexicon: Sequence[str], per: int = 200) -> float:
    """Distinct lexicon terms per ``per`` words (§3 R1).

    Counts *distinct* terms, not occurrences: repetition of one term is not
    expertise. Multi-word terms are counted when their folded form is a substring
    of the folded text.
    """
    ws = words(text)
    if not ws:
        return 0.0
    flat = squash(text)
    hits = {t for t in lexicon if squash(t) and squash(t) in flat}
    return len(hits) * per / len(ws)


def numeric_density(text: str) -> float:
    """Numbers with units per 80 words (§3 R3)."""
    ws = words(text)
    if not ws:
        return 0.0
    return len(NUMBER_WITH_UNIT.findall(text)) * 80 / len(ws)


def word_count(text: str) -> int:
    return len(words(text))


def mean_words_per_sentence(text: str) -> float:
    ss = sentences(text)
    if not ss:
        return 0.0
    return sum(len(words(s)) for s in ss) / len(ss)


def digit_style(text: str) -> str:
    """Which digit block the page actually uses — a reporting stratum, not a defect."""
    has_p = any(ch in PERSIAN_DIGITS for ch in text)
    has_a = any(ch in ARABIC_DIGITS for ch in text)
    has_l = bool(re.search(r"[0-9]", text))
    kinds = [n for n, flag in (("persian", has_p), ("arabic", has_a), ("latin", has_l)) if flag]
    if not kinds:
        return "none"
    return kinds[0] if len(kinds) == 1 else "mixed"


# ---------------------------------------------------------------------------
# Content gates
# ---------------------------------------------------------------------------
# Assistant/meta telltales. The KITAB-Bench contamination (Baseer §4) is the
# reason these are a hard gate: a leaked instruction string in ground truth is
# unrecoverable once it is in a benchmark.
_PERSIAN_ECHO = re.compile(
    r"(حتما|بله،? این متن|به شکل زیر|متن زیر (?:است|را)|در ادامه (?:متن|می‌آید)|"
    r"خوشحال می‌شوم|امیدوارم (?:این|کمک)|اگر (?:سوال|سؤال) دیگری)"
)
_ENGLISH_ECHO = re.compile(
    r"\b(you'?re right|let me|here is the text|here'?s the|as it appears|"
    r"maintaining the|right-to-left direction|sure,|i can'?t|i cannot|"
    r"below is the|note: i)\b",
    re.IGNORECASE,
)


@dataclass
class GateReport:
    """Verdict plus the numbers behind it. Nothing here edits the text."""

    verdict: str  # "accept" | "reject"
    reasons: list[str] = field(default_factory=list)
    banned_hits: list[str] = field(default_factory=list)
    echo_hits: list[str] = field(default_factory=list)
    term_density: float = 0.0
    num_density: float = 0.0
    mean_sentence_words: float = 0.0
    words: int = 0
    digits: str = "none"

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "banned_hits": self.banned_hits,
            "echo_hits": self.echo_hits,
            "term_density": round(self.term_density, 4),
            "num_density": round(self.num_density, 4),
            "mean_sentence_words": round(self.mean_sentence_words, 2),
            "words": self.words,
            "digits": self.digits,
        }


def find_banned_patterns(text: str, patterns: Iterable[str]) -> list[str]:
    """Return the banned phrases present, tolerant of ZWNJ and spacing variants."""
    folded = fold_for_matching(text)
    flat = squash(text)
    hits: list[str] = []
    for p in patterns:
        rx = _phrase_regex(p)
        if rx.search(folded) or (squash(p) and squash(p) in flat):
            hits.append(p)
    return hits


def find_echo(text: str) -> list[str]:
    folded = fold_for_matching(text)
    hits = [m.group(0) for m in _PERSIAN_ECHO.finditer(folded)]
    hits += [m.group(0) for m in _ENGLISH_ECHO.finditer(text)]
    return hits


def evaluate(
    text: str,
    *,
    banned: Iterable[str] = (),
    lexicon: Sequence[str] = (),
    min_term_density: float = 0.0,
    min_numeric_density: float = 0.0,
    sentence_words_range: tuple[float, float] | None = None,
    check_sentence_length: bool = True,
) -> GateReport:
    """Apply the deterministic content gates and return a verdict.

    Hard failures (banned pattern, assistant echo, empty text) reject outright.
    Soft signals (density too low, sentence rhythm off) are reported as reasons
    but *do not* reject: they route the sample to human review, because a
    threshold that rejects automatically is a threshold nobody validated.
    """
    reasons: list[str] = []
    banned_hits = find_banned_patterns(text, banned)
    echo_hits = find_echo(text)
    term_d = terminology_density(text, lexicon) if lexicon else 0.0
    num_d = numeric_density(text)
    msw = mean_words_per_sentence(text)
    wc = word_count(text)

    if not text.strip():
        reasons.append("empty_output")
    if banned_hits:
        reasons.append("banned_lay_pattern")
    if echo_hits:
        reasons.append("assistant_echo")
    if wc and wc < 40:
        reasons.append("too_short")
    if lexicon and term_d < min_term_density:
        reasons.append("low_terminology_density")
    if min_numeric_density and num_d < min_numeric_density:
        reasons.append("low_numeric_density")
    if sentence_words_range and check_sentence_length and wc:
        lo, hi = sentence_words_range
        if not (lo <= msw <= hi):
            reasons.append("sentence_rhythm_out_of_range")

    hard = {"empty_output", "banned_lay_pattern", "assistant_echo", "too_short"}
    verdict = "reject" if hard & set(reasons) else "accept"
    return GateReport(
        verdict=verdict,
        reasons=reasons,
        banned_hits=banned_hits,
        echo_hits=echo_hits,
        term_density=term_d,
        num_density=num_d,
        mean_sentence_words=msw,
        words=wc,
        digits=digit_style(text),
    )