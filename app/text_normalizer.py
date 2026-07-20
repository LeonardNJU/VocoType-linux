"""Text normalization helpers for ASR output."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
from typing import Sequence

try:
    from .term_lexicon import apply_term_lexicon
except ImportError:  # Support direct file loading used by standalone tests/tools.
    module_path = Path(__file__).with_name("term_lexicon.py")
    module_name = "_vocotype_term_lexicon"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    apply_term_lexicon = module.apply_term_lexicon


_DIGIT_MAP = {
    "零": "0",
    "〇": "0",
    "○": "0",
    "一": "1",
    "幺": "1",
    "二": "2",
    "两": "2",
    "俩": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}
_ZERO_DIGITS = {"零", "〇", "○"}
_NONZERO_DIGITS = "".join(ch for ch in _DIGIT_MAP if ch not in _ZERO_DIGITS)
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}
_DIGIT_CHARS = "".join(_DIGIT_MAP)
_INTEGER_BODY_CHARS = _DIGIT_CHARS + "".join(_SMALL_UNITS) + "".join(_LARGE_UNITS)
_DECIMAL_BODY_PATTERN = (
    f"[{re.escape(_INTEGER_BODY_CHARS)}]+(?:点[{re.escape(_DIGIT_CHARS)}]+)?"
)
_INTEGER_ONLY_PATTERN = f"[{re.escape(_INTEGER_BODY_CHARS)}]+"
_CANDIDATE_RE = re.compile(
    rf"百分之(?P<percent>{_DECIMAL_BODY_PATTERN})"
    rf"|第(?P<ordinal>{_INTEGER_ONLY_PATTERN})"
    rf"|负(?P<negative>{_DECIMAL_BODY_PATTERN})"
    rf"|(?P<general>{_DECIMAL_BODY_PATTERN})"
)
_APPROX_LEADING_UNIT_RE = re.compile(rf"[{re.escape(_NONZERO_DIGITS)}]{{2,}}[十百千万亿]")
_APPROX_TRAILING_DIGITS_RE = re.compile(rf"[十百千万亿][{re.escape(_NONZERO_DIGITS)}]{{2,}}")
_MEASURE_TOKENS = (
    "小时",
    "分钟",
    "秒钟",
    "公里",
    "平方",
    "立方",
    "厘米",
    "毫米",
    "公斤",
    "千克",
    "毫升",
    "页",
    "章",
    "节",
    "集",
    "篇",
    "句",
    "行",
    "列",
    "版",
    "代",
    "层",
    "楼",
    "次",
    "笔",
    "项",
    "套",
    "场",
    "遍",
    "周",
    "天",
    "年",
    "月",
    "日",
    "号",
    "点",
    "分",
    "秒",
    "米",
    "斤",
    "元",
    "块",
    "度",
    "折",
    "%",
    "％",
    "℃",
)
_SINGLE_DIGIT_TOKENS = tuple(token for token in _MEASURE_TOKENS if token != "个")
_CONTEXT_PREFIX_CHARS = set("到至和或比乘除加减约近超共用隔差")
_CONTEXT_SUFFIX_CHARS = set("到至和或比乘除加减多余前后")


Span = tuple[int, int]


def normalize_text(text: str, *, convert_chinese_numbers: bool = True) -> str:
    """Canonicalize configured terms, then normalize unprotected numerals."""

    term_result = apply_term_lexicon(text or "")
    normalized = term_result.text
    if convert_chinese_numbers and normalized:
        normalized = normalize_chinese_numbers(
            normalized,
            protected_spans=term_result.protected_spans,
        )
    return normalized


def normalize_chinese_numbers(
    text: str,
    *,
    protected_spans: Sequence[Span] = (),
) -> str:
    """Convert suitable Chinese numerals to Arabic digits."""

    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        start, end = match.span()
        prev_char = text[start - 1] if start > 0 else ""
        next_text = text[end:]
        full_match = match.group(0)

        if _span_overlaps_protected(start, end, protected_spans):
            return full_match

        if match.group("percent") is not None:
            converted = _convert_number_body(match.group("percent"))
            return f"{converted}%" if converted is not None else full_match

        if match.group("ordinal") is not None:
            converted = _convert_number_body(match.group("ordinal"))
            return f"第{converted}" if converted is not None else full_match

        if match.group("negative") is not None:
            converted = _convert_number_body(match.group("negative"))
            return f"-{converted}" if converted is not None else full_match

        body = match.group("general")
        if body is None or not _should_convert_general(body, prev_char=prev_char, next_text=next_text):
            return full_match
        converted = _convert_number_body(body)
        return converted if converted is not None else full_match

    return _CANDIDATE_RE.sub(_replace, text)



def _span_overlaps_protected(
    start: int,
    end: int,
    protected_spans: Sequence[Span],
) -> bool:
    return any(start < protected_end and end > protected_start for protected_start, protected_end in protected_spans)

def _should_convert_general(body: str, *, prev_char: str, next_text: str) -> bool:
    if not body:
        return False
    if _looks_like_approximate_phrase(body, next_text):
        return False

    has_units = any(ch in _SMALL_UNITS or ch in _LARGE_UNITS for ch in body)
    if "点" in body:
        return True
    if has_units:
        return True

    if len(body) >= 3:
        return True
    if len(body) == 2:
        if any(ch in _ZERO_DIGITS for ch in body):
            return True
        return _has_explicit_numeric_context(prev_char=prev_char, next_text=next_text)
    return _has_single_digit_context(prev_char=prev_char, next_text=next_text)


def _has_explicit_numeric_context(*, prev_char: str, next_text: str) -> bool:
    return _has_single_digit_context(prev_char=prev_char, next_text=next_text)


def _has_single_digit_context(*, prev_char: str, next_text: str) -> bool:
    if prev_char and prev_char in _CONTEXT_PREFIX_CHARS:
        return True
    next_char = next_text[:1]
    if next_char and next_char in _CONTEXT_SUFFIX_CHARS:
        return True
    if _starts_with_any(next_text, _SINGLE_DIGIT_TOKENS):
        if next_text.startswith("点"):
            return _looks_like_time_context(next_text)
        return True
    return False


def _looks_like_approximate_phrase(body: str, next_text: str) -> bool:
    if not body:
        return False
    if any(ch in _SMALL_UNITS or ch in _LARGE_UNITS for ch in body):
        return bool(
            _APPROX_LEADING_UNIT_RE.search(body) or _APPROX_TRAILING_DIGITS_RE.search(body)
        )
    if len(body) not in {2, 3}:
        return False
    if any(ch in _ZERO_DIGITS for ch in body):
        return False
    if len(set(body)) == 1:
        return False
    return _starts_with_any(next_text, _MEASURE_TOKENS)


def _looks_like_time_context(next_text: str) -> bool:
    if not next_text.startswith("点"):
        return False
    tail = next_text[1:]
    if not tail:
        return True
    if _starts_with_any(tail, ("钟", "整", "半", "过", "前", "后", "左右", "多")):
        return True
    return bool(tail[:1] and tail[0] in _DIGIT_MAP)


def _starts_with_any(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)


def _convert_number_body(body: str) -> str | None:
    if not body:
        return None
    if "点" in body:
        integer_part, fractional_part = body.split("点", 1)
        if not fractional_part or any(ch not in _DIGIT_MAP for ch in fractional_part):
            return None
        integer_value = _convert_integer_part(integer_part or "零")
        if integer_value is None:
            return None
        fractional_value = "".join(_DIGIT_MAP[ch] for ch in fractional_part)
        return f"{integer_value}.{fractional_value}"
    return _convert_integer_part(body)


def _convert_integer_part(body: str) -> str | None:
    if not body:
        return None
    if any(ch not in _INTEGER_BODY_CHARS for ch in body):
        return None
    if all(ch in _DIGIT_MAP for ch in body):
        return "".join(_DIGIT_MAP[ch] for ch in body)
    value = _parse_integer_value(body)
    return str(value) if value is not None else None


def _parse_integer_value(body: str) -> int | None:
    if not body:
        return None

    if "亿" in body:
        left, right = body.split("亿", 1)
        left_value = _parse_integer_value(left or "一")
        if left_value is None:
            return None
        right_value = _parse_large_unit_tail(right, _LARGE_UNITS["亿"])
        if right_value is None:
            return None
        return left_value * _LARGE_UNITS["亿"] + right_value

    if "万" in body:
        left, right = body.split("万", 1)
        left_value = _parse_integer_value(left or "一")
        if left_value is None:
            return None
        right_value = _parse_large_unit_tail(right, _LARGE_UNITS["万"])
        if right_value is None:
            return None
        return left_value * _LARGE_UNITS["万"] + right_value

    return _parse_section_value(body)


def _parse_large_unit_tail(body: str, large_unit: int) -> int | None:
    if not body:
        return 0
    if (
        all(ch in _DIGIT_MAP for ch in body)
        and len(body) <= 3
        and all(ch not in _ZERO_DIGITS for ch in body)
    ):
        digits = int("".join(_DIGIT_MAP[ch] for ch in body))
        return digits * (large_unit // (10 ** len(body)))
    return _parse_integer_value(body)


def _parse_section_value(body: str) -> int | None:
    if not body:
        return None
    if all(ch in _DIGIT_MAP for ch in body):
        return int("".join(_DIGIT_MAP[ch] for ch in body))

    total = 0
    number = 0
    saw_unit = False
    last_unit_value = 1
    trailing_zero_after_last_unit = False

    for char in body:
        if char in _DIGIT_MAP:
            digit = int(_DIGIT_MAP[char])
            if saw_unit and digit == 0:
                trailing_zero_after_last_unit = True
            number = digit
            continue

        unit = _SMALL_UNITS.get(char)
        if unit is None:
            return None

        if number == 0:
            number = 1 if unit == 10 and total == 0 else 0
        total += number * unit
        number = 0
        saw_unit = True
        last_unit_value = unit
        trailing_zero_after_last_unit = False

    total += number
    if (
        saw_unit
        and number
        and last_unit_value >= 100
        and not trailing_zero_after_last_unit
    ):
        total += number * ((last_unit_value // 10) - 1)
    return total
