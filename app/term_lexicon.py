"""Shared VoCoType terminology, hotword, and canonicalization support."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

import yaml


logger = logging.getLogger(__name__)

TERMS_FILE_ENV = "VOCOTYPE_TERMS_FILE"
TERMS_FILENAME = "terms.yaml"
LEGACY_DICTIONARY_FILENAME = "user-dictionary.yaml"
MAX_NATIVE_HOTWORDS = 1000
MAX_NATIVE_HOTWORD_CHARS = 10
Span = tuple[int, int]


@dataclass(frozen=True)
class TermRewriteResult:
    text: str
    protected_spans: tuple[Span, ...] = ()


@dataclass(frozen=True)
class TermEntry:
    canonical: str
    aliases: tuple[str, ...] = ()
    hotwords: tuple[str, ...] = ()
    protect: bool = True


@dataclass(frozen=True)
class TermLexicon:
    entries: tuple[TermEntry, ...] = ()
    protected_phrases: tuple[str, ...] = ()
    _replacement_pattern: re.Pattern[str] | None = None
    _replacement_map: tuple[tuple[str, str], ...] = ()

    def rewrite(self, text: str) -> TermRewriteResult:
        source = text or ""
        if not source:
            return TermRewriteResult("")

        rewritten = source
        replacement_spans: list[Span] = []
        if self._replacement_pattern is not None:
            chunks: list[str] = []
            source_end = 0
            output_length = 0
            replacement_map = dict(self._replacement_map)
            for match in self._replacement_pattern.finditer(source):
                prefix = source[source_end : match.start()]
                chunks.append(prefix)
                output_length += len(prefix)

                canonical = replacement_map[match.group(0).casefold()]
                chunks.append(canonical)
                replacement_spans.append(
                    (output_length, output_length + len(canonical))
                )
                output_length += len(canonical)
                source_end = match.end()

            chunks.append(source[source_end:])
            rewritten = "".join(chunks)

        phrase_spans = _find_phrase_spans(rewritten, self.protected_phrases)
        return TermRewriteResult(
            text=rewritten,
            protected_spans=_merge_spans((*replacement_spans, *phrase_spans)),
        )

    def native_hotwords(self) -> tuple[str, ...]:
        words: list[str] = []
        seen: set[str] = set()
        for entry in self.entries:
            for word in entry.hotwords:
                normalized = _normalize_hotword(word)
                if normalized is None:
                    continue
                key = normalized.casefold()
                if key in seen:
                    continue
                seen.add(key)
                words.append(normalized)
                if len(words) >= MAX_NATIVE_HOTWORDS:
                    logger.warning(
                        "原生热词超过上限，已截断为 %s 个",
                        MAX_NATIVE_HOTWORDS,
                    )
                    return tuple(words)
        return tuple(words)


@dataclass
class _LexiconCache:
    path: Path | None = None
    signature: tuple[int, int] | None = None
    lexicon: TermLexicon = TermLexicon()


_CACHE = _LexiconCache()


def get_terms_path() -> Path:
    override = os.environ.get(TERMS_FILE_ENV)
    if override:
        return Path(override).expanduser()

    config_home = os.environ.get("XDG_CONFIG_HOME")
    base_dir = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    config_dir = base_dir / "vocotype"
    preferred = config_dir / TERMS_FILENAME
    legacy = config_dir / LEGACY_DICTIONARY_FILENAME
    if not preferred.exists() and legacy.exists():
        return legacy
    return preferred


def load_term_lexicon() -> TermLexicon:
    path = get_terms_path()
    signature = _file_signature(path)
    previous_path = _CACHE.path
    previous_lexicon = _CACHE.lexicon

    if _CACHE.path == path and _CACHE.signature == signature:
        return _CACHE.lexicon

    if signature is None:
        _CACHE.path = path
        _CACHE.signature = None
        _CACHE.lexicon = TermLexicon()
        return _CACHE.lexicon

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.load(handle, Loader=yaml.BaseLoader)
        lexicon = compile_term_lexicon(raw)
    except Exception as exc:  # noqa: BLE001 - user config must not break typing.
        logger.warning("读取术语库失败: %s: %s", path, exc)
        _CACHE.path = path
        _CACHE.signature = signature
        _CACHE.lexicon = previous_lexicon if previous_path == path else TermLexicon()
        return _CACHE.lexicon

    _CACHE.path = path
    _CACHE.signature = signature
    _CACHE.lexicon = lexicon
    return lexicon


def apply_term_lexicon(text: str) -> TermRewriteResult:
    return load_term_lexicon().rewrite(text)


def build_native_hotword_string(extra_hotwords: str | Iterable[str] = "") -> str:
    """Build the Contextual Paraformer space-separated hotword string."""

    candidates = list(load_term_lexicon().native_hotwords())
    if isinstance(extra_hotwords, str):
        candidates.extend(extra_hotwords.split())
    else:
        candidates.extend(str(item) for item in extra_hotwords)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_hotword(candidate)
        if normalized is None:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= MAX_NATIVE_HOTWORDS:
            logger.warning("原生热词超过上限，已截断为 %s 个", MAX_NATIVE_HOTWORDS)
            break
    return " ".join(result)


def compile_term_lexicon(raw: Any) -> TermLexicon:
    if raw is None:
        return TermLexicon()
    if not isinstance(raw, dict):
        raise ValueError("术语库顶层必须是映射")

    entries: list[TermEntry] = []
    explicit_protected: list[str] = []

    raw_terms = raw.get("terms", [])
    if raw_terms is None:
        raw_terms = []
    if not isinstance(raw_terms, list):
        raise ValueError("terms 必须是数组")
    for index, raw_entry in enumerate(raw_terms):
        entries.append(_compile_entry(raw_entry, index=index))

    # Backwards-compatible import of Geequlim's replace/protect dictionary format.
    raw_replace = raw.get("replace", {})
    if raw_replace is None:
        raw_replace = {}
    if not isinstance(raw_replace, dict):
        raise ValueError("replace 必须是映射")
    for canonical, aliases in raw_replace.items():
        entries.append(
            TermEntry(
                canonical=_normalize_phrase(canonical, "replace 标准词"),
                aliases=_normalize_string_list(aliases, "replace 别名"),
                hotwords=(),
                protect=True,
            )
        )

    raw_protect = raw.get("protect", [])
    if raw_protect is None:
        raw_protect = []
    if not isinstance(raw_protect, list):
        raise ValueError("protect 必须是字符串数组")
    explicit_protected.extend(
        _normalize_phrase(item, "protect 词条") for item in raw_protect
    )

    return _build_lexicon(entries, explicit_protected)


def _compile_entry(raw: Any, *, index: int) -> TermEntry:
    if not isinstance(raw, dict):
        raise ValueError(f"terms[{index}] 必须是映射")

    canonical = _normalize_phrase(raw.get("canonical"), f"terms[{index}].canonical")
    aliases = _normalize_string_list(raw.get("aliases", []), f"{canonical}.aliases")
    protect = _normalize_bool(raw.get("protect", True), f"{canonical}.protect")

    hotword_config = raw.get("hotwords", raw.get("hotword", False))
    if _normalize_bool_like(hotword_config) is True:
        hotwords = (canonical,)
    elif _normalize_bool_like(hotword_config) is False:
        hotwords = ()
    else:
        hotwords = _normalize_string_list(hotword_config, f"{canonical}.hotwords")

    return TermEntry(
        canonical=canonical,
        aliases=aliases,
        hotwords=hotwords,
        protect=protect,
    )


def _build_lexicon(
    entries: Sequence[TermEntry], explicit_protected: Sequence[str]
) -> TermLexicon:
    replacement_map: dict[str, str] = {}
    replacement_aliases: dict[str, str] = {}
    protected: set[str] = set(explicit_protected)
    canonical_keys = {entry.canonical.casefold() for entry in entries}

    for entry in entries:
        if entry.protect:
            protected.add(entry.canonical)
        for alias in entry.aliases:
            alias_key = alias.casefold()
            if alias_key in canonical_keys and alias_key != entry.canonical.casefold():
                logger.warning(
                    "忽略会覆盖其他标准词的别名: alias=%s canonical=%s",
                    alias,
                    entry.canonical,
                )
                continue
            existing = replacement_map.get(alias_key)
            if existing is not None and existing != entry.canonical:
                logger.warning(
                    "术语别名冲突，保留首次映射: alias=%s kept=%s ignored=%s",
                    alias,
                    existing,
                    entry.canonical,
                )
                continue
            replacement_map[alias_key] = entry.canonical
            replacement_aliases.setdefault(alias_key, alias)

    sorted_aliases = sorted(
        replacement_aliases.values(), key=lambda value: (-len(value), value.casefold())
    )
    pattern = None
    if sorted_aliases:
        alternatives = [_alias_pattern(alias) for alias in sorted_aliases]
        pattern = re.compile("|".join(alternatives), flags=re.IGNORECASE)

    return TermLexicon(
        entries=tuple(entries),
        protected_phrases=tuple(
            sorted(protected, key=lambda value: (-len(value), value.casefold()))
        ),
        _replacement_pattern=pattern,
        _replacement_map=tuple(replacement_map.items()),
    )


def _alias_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    prefix = r"(?<![A-Za-z0-9_])" if _starts_ascii_word(alias) else ""
    suffix = r"(?![A-Za-z0-9_])" if _ends_ascii_word(alias) else ""
    return f"{prefix}{escaped}{suffix}"


def _starts_ascii_word(value: str) -> bool:
    return bool(value and re.match(r"[A-Za-z0-9_]", value[0]))


def _ends_ascii_word(value: str) -> bool:
    return bool(value and re.match(r"[A-Za-z0-9_]", value[-1]))


def _normalize_hotword(raw: Any) -> str | None:
    if not isinstance(raw, str):
        logger.warning("忽略非字符串原生热词: %r", raw)
        return None
    word = raw.strip()
    if not word:
        return None
    if any(char.isspace() for char in word):
        logger.warning("忽略包含空白的原生热词: %s", word)
        return None
    if len(word) > MAX_NATIVE_HOTWORD_CHARS:
        logger.warning(
            "忽略超过 %s 字符的原生热词: %s",
            MAX_NATIVE_HOTWORD_CHARS,
            word,
        )
        return None
    return word


def _normalize_string_list(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        raise ValueError(f"{label} 必须是字符串或字符串数组")
    return tuple(_normalize_phrase(value, label) for value in values)


def _normalize_phrase(raw: Any, label: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{label} 必须是字符串")
    value = raw.strip()
    if not value:
        raise ValueError(f"{label} 不能为空")
    return value


def _normalize_bool(raw: Any, label: str) -> bool:
    result = _normalize_bool_like(raw)
    if result is None:
        raise ValueError(f"{label} 必须是布尔值")
    return result


def _normalize_bool_like(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"true", "yes", "on", "1"}:
            return True
        if value in {"false", "no", "off", "0", ""}:
            return False
    return None


def _find_phrase_spans(text: str, phrases: Sequence[str]) -> tuple[Span, ...]:
    spans: list[Span] = []
    for phrase in phrases:
        flags = re.IGNORECASE if _contains_ascii_letter(phrase) else 0
        for match in re.finditer(re.escape(phrase), text, flags=flags):
            spans.append(match.span())
    return tuple(spans)


def _contains_ascii_letter(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value))


def _merge_spans(spans: Iterable[Span]) -> tuple[Span, ...]:
    normalized = sorted((start, end) for start, end in spans if start < end)
    if not normalized:
        return ()
    result: list[Span] = []
    current_start, current_end = normalized[0]
    for start, end in normalized[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            result.append((current_start, current_end))
            current_start, current_end = start, end
    result.append((current_start, current_end))
    return tuple(result)


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _reset_term_lexicon_cache() -> None:
    _CACHE.path = None
    _CACHE.signature = None
    _CACHE.lexicon = TermLexicon()
