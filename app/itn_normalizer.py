"""Mandatory, guarded Chinese inverse text normalization."""

from __future__ import annotations

import difflib
import logging
import re
import threading
from typing import Iterable, Sequence

from itn.chinese.inverse_normalizer import InverseNormalizer


logger = logging.getLogger(__name__)
Span = tuple[int, int]

_NORMALIZER: InverseNormalizer | None = None
_NORMALIZER_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_PRIVATE_USE_START = 0xE000
_PRIVATE_USE_END = 0xF8FF
_EXISTING_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+%#@-]+")
_REMOVED_SAFE_CHARS = frozenset(
    "零〇○一二两俩三四五六七八九幺洞拐勾十百千万亿点百分之千分之负正下"
)
_ADDED_SAFE_CHARS = frozenset("0123456789.%‰+-")
_SPOKEN_NUMERIC_CHARS = frozenset(
    "零〇○一二两俩三四五六七八九幺洞拐勾十百千万亿点"
)


def normalize_mandatory_itn(
    text: str,
    *,
    source_text: str | None = None,
    protected_spans: Sequence[Span] = (),
    fixed_phrases: Iterable[str] = (),
) -> str:
    """Always run Chinese FST ITN, accepting only semantics-safe changes.

    Product-specific numeric rules run first. Their changed spans, configured
    terminology, fixed idioms, and already-written ASCII tokens are masked so
    WeTextProcessing cannot restyle them. Remaining ITN changes are accepted
    only when they replace spoken numeric characters with digits and basic
    numeric punctuation; unit, date, time, currency, and range-style rewrites
    are rejected and left to the product policy.
    """

    normalized = text or ""
    if not normalized:
        return ""

    spans = list(protected_spans)
    if source_text is not None:
        spans.extend(_changed_output_spans(source_text, normalized))
    spans.extend(_phrase_spans(normalized, fixed_phrases))
    spans.extend(match.span() for match in _EXISTING_ASCII_TOKEN_RE.finditer(normalized))

    masked, replacements = _mask_spans(normalized, spans)
    with _RUN_LOCK:
        candidate = _get_normalizer().normalize(masked)
    candidate = _restore_masks(candidate, replacements)

    if candidate == normalized:
        return normalized
    if _is_safe_itn_change(normalized, candidate):
        return candidate

    logger.debug("拒绝可能改变格式或语义的 ITN 输出: %r -> %r", normalized, candidate)
    return normalized


def _get_normalizer() -> InverseNormalizer:
    global _NORMALIZER
    if _NORMALIZER is not None:
        return _NORMALIZER
    with _NORMALIZER_LOCK:
        if _NORMALIZER is None:
            _NORMALIZER = InverseNormalizer(
                remove_interjections=False,
                enable_standalone_number=True,
                enable_0_to_9=False,
                enable_million=False,
            )
    return _NORMALIZER


def _changed_output_spans(source: str, output: str) -> tuple[Span, ...]:
    matcher = difflib.SequenceMatcher(a=source, b=output, autojunk=False)
    return tuple(
        (out_start, out_end)
        for tag, _src_start, _src_end, out_start, out_end in matcher.get_opcodes()
        if tag != "equal" and out_start < out_end
    )


def _phrase_spans(text: str, phrases: Iterable[str]) -> tuple[Span, ...]:
    spans: list[Span] = []
    for phrase in phrases:
        if not phrase:
            continue
        for match in re.finditer(re.escape(str(phrase)), text):
            start, end = match.span()
            # A fixed phrase may itself look numeric (e.g. “二百五”). Do not
            # protect it when it is merely a substring of a larger number.
            if start > 0 and text[start - 1] in _SPOKEN_NUMERIC_CHARS:
                continue
            if end < len(text) and text[end] in _SPOKEN_NUMERIC_CHARS:
                continue
            spans.append((start, end))
    return tuple(spans)


def _mask_spans(text: str, spans: Iterable[Span]) -> tuple[str, tuple[tuple[str, str], ...]]:
    merged = _merge_spans(spans)
    if not merged:
        return text, ()

    used = set(text)
    placeholders = (
        chr(codepoint)
        for codepoint in range(_PRIVATE_USE_START, _PRIVATE_USE_END + 1)
        if chr(codepoint) not in used
    )
    chunks: list[str] = []
    replacements: list[tuple[str, str]] = []
    position = 0
    for start, end in merged:
        try:
            placeholder = next(placeholders)
        except StopIteration as exc:
            raise ValueError("文本中的保护片段过多，无法分配 ITN 占位符") from exc
        chunks.append(text[position:start])
        chunks.append(placeholder)
        replacements.append((placeholder, text[start:end]))
        position = end
    chunks.append(text[position:])
    return "".join(chunks), tuple(replacements)


def _restore_masks(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    restored = text
    for placeholder, value in replacements:
        restored = restored.replace(placeholder, value)
    return restored


def _is_safe_itn_change(source: str, candidate: str) -> bool:
    matcher = difflib.SequenceMatcher(a=source, b=candidate, autojunk=False)
    saw_change = False
    for tag, src_start, src_end, out_start, out_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        saw_change = True
        removed = source[src_start:src_end]
        added = candidate[out_start:out_end]
        if any(not (char.isspace() or char in _REMOVED_SAFE_CHARS) for char in removed):
            return False
        if any(not (char.isspace() or char in _ADDED_SAFE_CHARS) for char in added):
            return False
    return saw_change


def _merge_spans(spans: Iterable[Span]) -> tuple[Span, ...]:
    normalized = sorted((start, end) for start, end in spans if 0 <= start < end)
    if not normalized:
        return ()
    merged: list[Span] = []
    start, end = normalized[0]
    for next_start, next_end in normalized[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            merged.append((start, end))
            start, end = next_start, next_end
    merged.append((start, end))
    return tuple(merged)


def _reset_itn_for_tests() -> None:
    global _NORMALIZER
    with _NORMALIZER_LOCK:
        _NORMALIZER = None
