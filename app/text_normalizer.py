"""Text normalization helpers for ASR output."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from runpy import run_path
import sys
from typing import Sequence

try:
    from .itn_normalizer import normalize_mandatory_itn
    from .term_lexicon import apply_term_lexicon
except ImportError:
    def _load_sibling(module_name: str, filename: str):
        module = sys.modules.get(module_name)
        if module is not None:
            return module
        module_path = Path(__file__).with_name(filename)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载 {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    normalize_mandatory_itn = _load_sibling(
        "_vocotype_itn_normalizer", "itn_normalizer.py"
    ).normalize_mandatory_itn
    apply_term_lexicon = _load_sibling(
        "_vocotype_term_lexicon", "term_lexicon.py"
    ).apply_term_lexicon



def _load_fixed_non_numeric_phrases() -> dict[str, str]:
    phrases_path = Path(__file__).with_name("text_normalizer_phrases.py")
    namespace = run_path(str(phrases_path))
    phrases = namespace.get("FIXED_NON_NUMERIC_PHRASES", {})
    return dict(phrases)


_VALUE_DIGIT_MAP = {
    "零": "0",
    "〇": "0",
    "○": "0",
    "一": "1",
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
_SEQUENCE_DIGIT_MAP = {
    **_VALUE_DIGIT_MAP,
    "幺": "1",
    "洞": "0",
    "拐": "7",
    "勾": "9",
}
_ZERO_DIGITS = {"零", "〇", "○", "洞"}
_VALUE_ZERO_DIGITS = {"零", "〇", "○"}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}
_VALUE_DIGIT_CHARS = "".join(_VALUE_DIGIT_MAP)
_SEQUENCE_DIGIT_CHARS = "".join(_SEQUENCE_DIGIT_MAP)
_NONZERO_DIGITS = "".join(ch for ch, digit in _VALUE_DIGIT_MAP.items() if digit != "0")
_INTEGER_BODY_CHARS = _VALUE_DIGIT_CHARS + "".join(_SMALL_UNITS) + "".join(_LARGE_UNITS)
_GENERAL_BODY_CHARS = _SEQUENCE_DIGIT_CHARS + "".join(_SMALL_UNITS) + "".join(_LARGE_UNITS)
_DECIMAL_SUFFIX_GUARD = f"(?![{re.escape(''.join(_SMALL_UNITS) + ''.join(_LARGE_UNITS))}])"
_DECIMAL_BODY_PATTERN = (
    f"[{re.escape(_INTEGER_BODY_CHARS)}]+"
    f"(?:点[{re.escape(_VALUE_DIGIT_CHARS)}]+{_DECIMAL_SUFFIX_GUARD})?"
)
_INTEGER_ONLY_PATTERN = f"[{re.escape(_INTEGER_BODY_CHARS)}]+"
_GENERAL_BODY_PATTERN = (
    f"[{re.escape(_GENERAL_BODY_CHARS)}]+"
    f"(?:点[{re.escape(_VALUE_DIGIT_CHARS)}]+{_DECIMAL_SUFFIX_GUARD})?"
)
_CANDIDATE_RE = re.compile(
    rf"(?P<money_sign>负)?(?P<money_whole>{_INTEGER_ONLY_PATTERN})"
    rf"(?P<money_unit>块|元)(?P<money_fraction>[{re.escape(_VALUE_DIGIT_CHARS)}])"
    rf"|负百分之(?P<negative_percent>{_DECIMAL_BODY_PATTERN})"
    rf"|百分之(?P<percent>{_DECIMAL_BODY_PATTERN})"
    rf"|千分之(?P<permille>{_DECIMAL_BODY_PATTERN})"
    rf"|零下(?P<below_zero>{_DECIMAL_BODY_PATTERN})"
    rf"|(?P<time_hour>{_INTEGER_ONLY_PATTERN})点(?P<time_minute>{_INTEGER_ONLY_PATTERN})分"
    rf"|第(?P<ordinal>{_INTEGER_ONLY_PATTERN})"
    rf"|正(?P<positive>{_DECIMAL_BODY_PATTERN})"
    rf"|负(?P<negative>{_DECIMAL_BODY_PATTERN})"
    rf"|(?P<general>{_GENERAL_BODY_PATTERN})"
)
_APPROX_LEADING_UNIT_RE = re.compile(rf"[{re.escape(_NONZERO_DIGITS)}]{{2,}}[十百千万亿]")
_APPROX_TRAILING_DIGITS_RE = re.compile(rf"[十百千万亿][{re.escape(_NONZERO_DIGITS)}]{{2,}}")
_APPROX_MEASURE_TOKENS = (
    "小时",
    "分钟",
    "秒钟",
    "公里",
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
    "人",
    "斤",
    "元",
    "块",
    "度",
    "折",
    "个",
    "岁",
    "下",
    "%",
    "％",
    "℃",
)
_COUNT_CLASSIFIER_TOKENS = ("个", "盒", "件", "行", "关")
_NUMERIC_SUFFIX_TOKENS = ("以内", "以上", "以下", "左右")
_SEMANTIC_NUMERIC_PREFIXES = (
    "库存",
    "库存还有",
    "宽度",
    "高度",
    "内存限制",
    "限制",
    "评分",
    "日志保留",
    "保留",
    "活动持续",
    "持续",
    "总价",
    "预算",
    "坐标",
    "逗号",
)

_QUANTITY_UNIT_TOKENS = (
    "平方米",
    "立方米",
    "小时",
    "分钟",
    "秒钟",
    "公里",
    "厘米",
    "毫米",
    "公斤",
    "毫升",
    "平方",
    "立方",
    "页",
    "章",
    "节",
    "集",
    "篇",
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
    "米",
    "人",
    "斤",
    "元",
    "块",
    "度",
    "折",
    "秒",
    "克",
    "兆",
    "岁",
    "%",
    "％",
    "℃",
)
_DATE_SUFFIXES = ("年", "月", "日", "号")
_TIME_POINT_SUFFIXES = ("点钟", "点整", "点半", "点过", "点前", "点后", "点左右", "点多", "点")
_DIGIT_SEQUENCE_PREFIXES = (
    "手机号",
    "手机号码",
    "电话号码",
    "电话",
    "验证码",
    "校验码",
    "编号",
    "号码",
    "账号",
    "帐号",
    "账户",
    "工号",
    "单号",
    "订单号",
    "订单",
    "快递单号",
    "快递",
    "邮编",
    "端口号",
    "端口",
    "进程号",
    "状态码",
    "房号",
    "房间号",
    "尾号",
    "频道号",
    "工位编号",
    "车牌",
    "ID",
    "id",
)
_FULL_NUMBER_DISPLAY_PREFIXES = (
    *_DIGIT_SEQUENCE_PREFIXES,
    "最大连接数",
    "连接数",
)
_DIGIT_SEQUENCE_SUFFIXES = ("端口", "错误")
_PLACE_NUMBER_SUFFIXES = ("号会议室", "号机房", "号门", "号位")
_NUMERIC_PREFIX_TOKENS = (
    "等于",
    "等於",
    "设置成",
    "设置为",
    "设为",
    "改成",
    "改为",
    "调成",
    "调到",
    "调整成",
    "调整为",
)
_MATH_PREFIX_TOKENS = ("乘以", "除以", "等于", "等於", "加", "减")
_MATH_SUFFIX_TOKENS = ("乘以", "除以", "等于", "等於", "次方", "加", "减")
_TIME_EVENT_SUFFIXES = ("开会", "更新", "上线", "发布", "提醒", "重试", "执行", "开服")
_DURATION_HALF_SUFFIXES = ("分半",)
_CONTEXT_PREFIX_CHARS = set("到至和或比乘除加减约近超共用隔差")
_CONTEXT_SUFFIX_CHARS = set("到至和或比乘除加减多余前后")
_CONTEXT_SEPARATOR_CHARS = " \t\r\n:：#-—_,，是为"
_STANDALONE_PREFIX_BOUNDARY_CHARS = " \t\r\n([{\"'“‘（【《「『"
_STANDALONE_SUFFIX_BOUNDARY_CHARS = " \t\r\n.,!?;:，。！？；：、)}\"'”’）】》」』…"
_FIXED_NON_NUMERIC_PHRASES = _load_fixed_non_numeric_phrases()


Span = tuple[int, int]


def normalize_text(text: str) -> str:
    """Run terminology, product numeric policy, then mandatory guarded ITN."""

    term_result = apply_term_lexicon(text or "")
    source = term_result.text
    if not source:
        return ""

    product_normalized = normalize_chinese_numbers(
        source,
        protected_spans=term_result.protected_spans,
    )
    # Re-evaluate protected spans after numeric replacements shift offsets.
    refreshed_terms = apply_term_lexicon(product_normalized)
    return normalize_mandatory_itn(
        refreshed_terms.text,
        source_text=source,
        protected_spans=refreshed_terms.protected_spans,
        fixed_phrases=_FIXED_NON_NUMERIC_PHRASES,
    )


def normalize_chinese_numbers(
    text: str,
    *,
    protected_spans: Sequence[Span] = (),
) -> str:
    """Convert Chinese numerals only when the surrounding text is numeric."""

    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        start, end = match.span()
        prev_text = text[:start]
        next_text = text[end:]
        full_match = match.group(0)

        if _span_overlaps_protected(start, end, protected_spans):
            return full_match

        if match.group("money_whole") is not None:
            whole = _convert_structured_number_body(match.group("money_whole"))
            fraction = _VALUE_DIGIT_MAP.get(match.group("money_fraction"))
            if whole is None or fraction is None:
                return full_match
            sign = "-" if match.group("money_sign") else ""
            return f"{sign}{whole}.{fraction}{match.group('money_unit')}"

        if match.group("negative_percent") is not None:
            converted = _convert_structured_number_body(match.group("negative_percent"))
            return f"-{converted}%" if converted is not None else full_match

        if match.group("percent") is not None:
            converted = _convert_structured_number_body(match.group("percent"))
            return f"{converted}%" if converted is not None else full_match

        if match.group("permille") is not None:
            converted = _convert_structured_number_body(match.group("permille"))
            return f"{converted}‰" if converted is not None else full_match

        if match.group("below_zero") is not None:
            converted = _convert_structured_number_body(match.group("below_zero"))
            return f"零下{converted}" if converted is not None else full_match

        if match.group("time_hour") is not None:
            hour = _convert_structured_number_body(match.group("time_hour"))
            minute = _convert_time_minute(match.group("time_minute"))
            if hour is None or minute is None:
                return full_match
            return f"{hour}点{minute}分"

        if match.group("ordinal") is not None:
            converted = _convert_structured_number_body(match.group("ordinal"))
            return f"第{converted}" if converted is not None else full_match

        if match.group("positive") is not None:
            if not _has_positive_number_context(prev_text=prev_text, next_text=next_text):
                return full_match
            converted = _convert_structured_number_body(match.group("positive"))
            return f"正{converted}" if converted is not None else full_match

        if match.group("negative") is not None:
            converted = _convert_structured_number_body(match.group("negative"))
            if converted is None:
                return full_match
            if _starts_with_any(next_text, ("楼", "层")):
                return f"负{converted}"
            return f"-{converted}"

        body = match.group("general")
        if body is None:
            return full_match

        split_body, preserved_suffix = _split_trailing_yigong_marker(body, next_text)
        if not _should_convert_general(
            split_body,
            prev_text=prev_text,
            next_text=f"{preserved_suffix}{next_text}",
        ):
            return full_match
        converted = _convert_general_body(
            split_body,
            force_full_number=_has_full_number_display_context(prev_text),
        )
        return f"{converted}{preserved_suffix}" if converted is not None else full_match

    return _CANDIDATE_RE.sub(_replace, text)


def _span_overlaps_protected(
    start: int,
    end: int,
    protected_spans: Sequence[Span],
) -> bool:
    return any(span_start < end and start < span_end for span_start, span_end in protected_spans)


def _should_convert_general(body: str, *, prev_text: str, next_text: str) -> bool:
    if not body:
        return False
    if "点" in body:
        return _is_valid_decimal(body)
    if _looks_like_fixed_phrase(body=body, prev_text=prev_text, next_text=next_text):
        return False
    if body == "一" and next_text == "点" and not _has_time_prefix_context(prev_text):
        return False
    if next_text.startswith("两"):
        return False
    if _looks_like_approximate_phrase(body, next_text):
        return False
    if _is_digit_sequence_body(body):
        return _should_convert_digit_sequence(body, prev_text=prev_text, next_text=next_text)
    if not _is_structured_numeric_body(body):
        return False
    if _has_spoken_large_unit_body(body) and _has_large_unit_shorthand_tail(body):
        return True
    if _has_full_number_display_context(prev_text):
        return True
    if _is_standalone_structured_number(body, prev_text=prev_text, next_text=next_text):
        return True
    if _is_date_context(body, prev_text=prev_text, next_text=next_text):
        return True
    if _is_place_number_context(next_text):
        return True
    if _is_time_context(next_text):
        return True
    if _is_duration_half_context(next_text):
        return True
    if _has_math_context(prev_text=prev_text, next_text=next_text):
        return True
    if _has_numeric_operator_context(prev_text=prev_text, next_text=next_text):
        return True
    if _starts_with_any(next_text, _COUNT_CLASSIFIER_TOKENS):
        return True
    if _starts_with_any(next_text, _NUMERIC_SUFFIX_TOKENS):
        return True
    if _has_semantic_numeric_prefix(prev_text):
        return True
    if _starts_with_any(next_text, _QUANTITY_UNIT_TOKENS):
        return True
    if _has_numeric_prefix_context(prev_text):
        return True
    return False


def _should_convert_digit_sequence(body: str, *, prev_text: str, next_text: str) -> bool:
    if _has_digit_sequence_context(prev_text):
        return True
    if len(body) >= 3 and _starts_with_any(next_text, _DIGIT_SEQUENCE_SUFFIXES):
        return True
    if _is_date_context(body, prev_text=prev_text, next_text=next_text):
        return True
    if _is_place_number_context(next_text):
        return True
    if _is_time_context(next_text):
        return True
    if _is_duration_half_context(next_text):
        return True
    if _has_math_context(prev_text=prev_text, next_text=next_text):
        return True
    if _has_numeric_operator_context(prev_text=prev_text, next_text=next_text):
        return True
    if _starts_with_any(next_text, _COUNT_CLASSIFIER_TOKENS):
        return True
    if _starts_with_any(next_text, _NUMERIC_SUFFIX_TOKENS):
        return True
    if _has_semantic_numeric_prefix(prev_text):
        return True
    if _starts_with_any(next_text, _QUANTITY_UNIT_TOKENS):
        return True
    if _has_numeric_prefix_context(prev_text):
        return True
    if len(body) >= 3 and _has_standalone_numeric_context(prev_text=prev_text, next_text=next_text):
        return True
    if len(body) >= 3 and next_text.startswith(("共", "一共")):
        return True
    return False


def _split_trailing_yigong_marker(body: str, next_text: str) -> tuple[str, str]:
    if (
        next_text.startswith("共")
        and len(body) > 1
        and body.endswith("一")
        and _is_digit_sequence_body(body[:-1])
    ):
        return body[:-1], "一"
    return body, ""


def _is_valid_decimal(body: str) -> bool:
    integer_part, separator, fractional_part = body.partition("点")
    return bool(
        separator
        and integer_part
        and fractional_part
        and _is_structured_numeric_body(integer_part)
        and all(ch in _VALUE_DIGIT_MAP for ch in fractional_part)
    )


def _is_digit_sequence_body(body: str) -> bool:
    return bool(body) and all(ch in _SEQUENCE_DIGIT_MAP for ch in body)


def _is_structured_numeric_body(body: str) -> bool:
    return bool(body) and all(ch in _INTEGER_BODY_CHARS for ch in body)


def _has_spoken_large_unit_body(body: str) -> bool:
    return any(ch in _LARGE_UNITS for ch in body)


def _has_large_unit_shorthand_tail(body: str) -> bool:
    first_large_unit_index = min(
        (index for index, char in enumerate(body) if char in _LARGE_UNITS),
        default=-1,
    )
    if first_large_unit_index <= 0:
        return False
    return bool(body[first_large_unit_index + 1 :])


def _is_standalone_structured_number(body: str, *, prev_text: str, next_text: str) -> bool:
    return (
        _has_standalone_numeric_context(prev_text=prev_text, next_text=next_text)
        and _contains_unit(body)
        and not _looks_like_approximate_phrase(body, next_text)
    )


def _has_standalone_numeric_context(*, prev_text: str, next_text: str) -> bool:
    return _has_standalone_prefix_boundary(prev_text) and _has_standalone_suffix_boundary(next_text)


def _has_standalone_prefix_boundary(prev_text: str) -> bool:
    return all(ch in _STANDALONE_PREFIX_BOUNDARY_CHARS for ch in prev_text)


def _has_standalone_suffix_boundary(next_text: str) -> bool:
    return all(ch in _STANDALONE_SUFFIX_BOUNDARY_CHARS for ch in next_text)


def _looks_like_fixed_phrase(*, body: str, prev_text: str, next_text: str) -> bool:
    if _matches_fixed_non_numeric_phrase(body=body, prev_text=prev_text, next_text=next_text):
        return True
    if body == "一" and prev_text.endswith("更上") and next_text.startswith("层楼"):
        return True
    if body == "十" and not prev_text and next_text.startswith("年生死"):
        return True
    if prev_text.endswith("波") and next_text.startswith("折"):
        return True
    if prev_text.endswith("番") and next_text.startswith("次"):
        return True
    if not next_text.startswith("斤"):
        return False

    tail = next_text[1:]
    return len(tail) >= 2 and tail.endswith("两") and all(ch in _VALUE_DIGIT_MAP for ch in tail[:-1])


def _matches_fixed_non_numeric_phrase(*, body: str, prev_text: str, next_text: str) -> bool:
    text = f"{prev_text}{body}{next_text}"
    body_start = len(prev_text)
    body_end = body_start + len(body)
    for phrase in _FIXED_NON_NUMERIC_PHRASES:
        phrase_start = text.find(phrase)
        while phrase_start != -1:
            phrase_end = phrase_start + len(phrase)
            if phrase_start <= body_start and body_end <= phrase_end:
                return True
            phrase_start = text.find(phrase, phrase_start + 1)
    return False


def _has_time_prefix_context(prev_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    return stripped.endswith(
        (
            "凌晨",
            "早上",
            "上午",
            "中午",
            "下午",
            "晚上",
            "夜里",
            "今天",
            "明天",
            "后天",
            "昨天",
        )
    )


def _is_date_context(body: str, *, prev_text: str, next_text: str) -> bool:
    suffix = _first_matching_prefix(next_text, _DATE_SUFFIXES)
    if suffix is None:
        return False
    if suffix == "月" and next_text.startswith("月天"):
        return False
    if suffix == "年":
        return len(body) >= 2 or _contains_unit(body)
    if suffix == "月":
        return True
    if suffix in {"日", "号"}:
        return len(body) >= 2 or _previous_date_suffix(prev_text)
    return False


def _previous_date_suffix(prev_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    return bool(stripped and stripped[-1] in {"年", "月"})


def _is_place_number_context(next_text: str) -> bool:
    return _starts_with_any(next_text, _PLACE_NUMBER_SUFFIXES)


def _is_time_context(next_text: str) -> bool:
    if not _starts_with_any(next_text, _TIME_POINT_SUFFIXES):
        return False
    tail = next_text[1:]
    if not tail:
        return True
    if _starts_with_any(tail, ("钟", "整", "半", "过", "前", "后", "左右", "多")):
        return True
    if _starts_with_any(tail, _TIME_EVENT_SUFFIXES):
        return True
    return bool(tail[:1] and tail[0] in _VALUE_DIGIT_MAP)


def _has_math_context(*, prev_text: str, next_text: str) -> bool:
    raw_prev = prev_text.rstrip(" \t\r\n:：#-—_,，")
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    if any(stripped.endswith(prefix) for prefix in _MATH_PREFIX_TOKENS):
        return True
    if _starts_with_any(next_text, _MATH_SUFFIX_TOKENS):
        return True
    if next_text.startswith("的") and "次方" in next_text[:5]:
        return True
    return raw_prev.endswith("是") and "次方" in raw_prev[-8:]


def _is_duration_half_context(next_text: str) -> bool:
    return _starts_with_any(next_text, _DURATION_HALF_SUFFIXES)


def _has_positive_number_context(*, prev_text: str, next_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    return bool(
        stripped.endswith(("到", "至"))
        or _has_math_context(prev_text=prev_text, next_text=next_text)
    )


def _has_numeric_operator_context(*, prev_text: str, next_text: str) -> bool:
    prev_char = prev_text[-1:] if prev_text else ""
    next_char = next_text[:1]
    return bool(
        (prev_char and prev_char in _CONTEXT_PREFIX_CHARS)
        or (next_char and next_char in _CONTEXT_SUFFIX_CHARS)
    )


def _has_semantic_numeric_prefix(prev_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    return any(stripped.endswith(prefix) for prefix in _SEMANTIC_NUMERIC_PREFIXES)


def _has_numeric_prefix_context(prev_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    if any(stripped.endswith(prefix) for prefix in _NUMERIC_PREFIX_TOKENS):
        return True
    prev_char = prev_text[-1:] if prev_text else ""
    return bool(prev_char and prev_char in _CONTEXT_PREFIX_CHARS)


def _has_digit_sequence_context(prev_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    return any(stripped.endswith(prefix) for prefix in _DIGIT_SEQUENCE_PREFIXES)


def _has_full_number_display_context(prev_text: str) -> bool:
    stripped = prev_text.rstrip(_CONTEXT_SEPARATOR_CHARS)
    return any(stripped.endswith(prefix) for prefix in _FULL_NUMBER_DISPLAY_PREFIXES)


def _looks_like_approximate_phrase(body: str, next_text: str) -> bool:
    if not body or "点" in body:
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
    return _starts_with_any(next_text, _APPROX_MEASURE_TOKENS)


def _convert_general_body(body: str, *, force_full_number: bool = False) -> str | None:
    if _is_digit_sequence_body(body):
        return _convert_digit_sequence(body)
    return _convert_structured_number_body(body, force_full_number=force_full_number)


def _convert_digit_sequence(body: str) -> str:
    return "".join(_SEQUENCE_DIGIT_MAP[ch] for ch in body)


def _convert_time_minute(body: str) -> str | None:
    if not body or any(ch not in _INTEGER_BODY_CHARS for ch in body):
        return None
    if all(ch in _VALUE_DIGIT_MAP for ch in body):
        return "".join(_VALUE_DIGIT_MAP[ch] for ch in body)
    value = _parse_integer_value(body)
    if value is None:
        return None
    return f"{value:02d}" if body[0] in _VALUE_ZERO_DIGITS else str(value)


def _convert_structured_number_body(
    body: str,
    *,
    force_full_number: bool = False,
) -> str | None:
    if not body:
        return None
    if "点" in body:
        integer_part, fractional_part = body.split("点", 1)
        if not fractional_part or any(ch not in _VALUE_DIGIT_MAP for ch in fractional_part):
            return None
        integer_value = _convert_integer_part(integer_part or "零")
        if integer_value is None:
            return None
        fractional_value = "".join(_VALUE_DIGIT_MAP[ch] for ch in fractional_part)
        return f"{integer_value}.{fractional_value}"
    large_unit_display = _convert_large_unit_display(
        body,
        force_full_number=force_full_number,
    )
    if large_unit_display is not None:
        return large_unit_display
    return _convert_integer_part(body)


def _convert_large_unit_display(
    body: str,
    *,
    force_full_number: bool = False,
) -> str | None:
    if force_full_number:
        return _convert_integer_part(body)

    if "亿" in body:
        left, right = body.split("亿", 1)
        left_display = _convert_integer_part(left or "一")
        if left_display is None:
            return None
        if not right:
            return f"{left_display}亿"
        total_value = _parse_integer_value(body)
        if total_value is None:
            return None
        if _is_short_large_unit_tail(right) or _is_wan_level_tail(right):
            return _format_large_unit_value(total_value, _LARGE_UNITS["亿"], "亿")
        return str(total_value)

    if "万" not in body:
        return None

    left, right = body.split("万", 1)
    if not left:
        return None
    left_display = _convert_integer_part(left)
    if left_display is None:
        return None
    if not right:
        return f"{left_display}万"
    total_value = _parse_integer_value(body)
    if total_value is None:
        return None
    if _is_short_large_unit_tail(right):
        return _format_large_unit_value(total_value, _LARGE_UNITS["万"], "万")
    return str(total_value)


def _format_large_unit_value(value: int, unit: int, suffix: str) -> str:
    whole, remainder = divmod(value, unit)
    if remainder == 0:
        return f"{whole}{suffix}"
    fractional = f"{remainder:0{len(str(unit)) - 1}d}".rstrip("0")
    return f"{whole}.{fractional}{suffix}"


def _is_wan_level_tail(body: str) -> bool:
    return (
        bool(body)
        and body.endswith("万")
        and all(ch not in _VALUE_ZERO_DIGITS for ch in body)
    )


def _is_short_large_unit_tail(body: str) -> bool:
    return (
        bool(body)
        and len(body) <= 3
        and all(ch in _VALUE_DIGIT_MAP for ch in body)
        and all(ch not in _VALUE_ZERO_DIGITS for ch in body)
    )


def _convert_integer_part(body: str) -> str | None:
    if not body:
        return None
    if any(ch not in _INTEGER_BODY_CHARS for ch in body):
        return None
    if all(ch in _VALUE_DIGIT_MAP for ch in body):
        return "".join(_VALUE_DIGIT_MAP[ch] for ch in body)
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
        all(ch in _VALUE_DIGIT_MAP for ch in body)
        and len(body) <= 3
        and all(ch not in _VALUE_ZERO_DIGITS for ch in body)
    ):
        digits = int("".join(_VALUE_DIGIT_MAP[ch] for ch in body))
        return digits * (large_unit // (10 ** len(body)))
    return _parse_integer_value(body)


def _parse_section_value(body: str) -> int | None:
    if not body:
        return None
    if all(ch in _VALUE_DIGIT_MAP for ch in body):
        return int("".join(_VALUE_DIGIT_MAP[ch] for ch in body))

    total = 0
    number = 0
    saw_unit = False
    last_unit_value = 1
    trailing_zero_after_last_unit = False

    for char in body:
        if char in _VALUE_DIGIT_MAP:
            digit = int(_VALUE_DIGIT_MAP[char])
            if saw_unit and digit == 0:
                trailing_zero_after_last_unit = True
            number = digit
            continue

        unit = _SMALL_UNITS.get(char)
        if unit is None:
            return None

        if number == 0:
            number = 1 if total == 0 else 0
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


def _contains_unit(body: str) -> bool:
    return any(ch in _SMALL_UNITS or ch in _LARGE_UNITS for ch in body)


def _first_matching_prefix(text: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if text.startswith(prefix):
            return prefix
    return None


def _starts_with_any(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)
