from __future__ import annotations

from pathlib import Path

import pytest

from app import term_lexicon
from app.text_normalizer import normalize_text


@pytest.fixture
def terms_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "terms.yaml"
    monkeypatch.setenv(term_lexicon.TERMS_FILE_ENV, str(path))
    term_lexicon._reset_term_lexicon_cache()
    yield path
    term_lexicon._reset_term_lexicon_cache()


def write_terms(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_missing_terms_file_is_noop(terms_path: Path):
    assert normalize_text("鬼斯提版本一点二") == "鬼斯提版本1.2"


def test_terms_replace_longest_alias_once_and_ignore_case(terms_path: Path):
    write_terms(
        terms_path,
        """
terms:
  - canonical: README
    aliases: [read me, readme]
  - canonical: README.md
    aliases: [read me点md, README文件]
  - canonical: Ghostty
    aliases: [ghostty, 鬼斯提]
""",
    )

    assert normalize_text("read me点md 和 GHOSTTY") == "README.md 和 Ghostty"
    assert normalize_text("Ghostty") == "Ghostty"


def test_ascii_aliases_respect_token_boundaries_and_are_idempotent(terms_path: Path):
    write_terms(
        terms_path,
        """
terms:
  - canonical: NoSQL
    aliases: [no]
  - canonical: NodeJS
    aliases: [node js]
""",
    )

    assert normalize_text("no 数据库") == "NoSQL 数据库"
    assert normalize_text("nobody") == "nobody"
    assert normalize_text("anode js") == "anode js"
    assert normalize_text("NoSQL") == "NoSQL"


def test_replacements_are_single_pass_not_recursive(terms_path: Path):
    write_terms(
        terms_path,
        """
terms:
  - canonical: B
    aliases: [alpha]
  - canonical: C
    aliases: [B]
""",
    )

    assert normalize_text("alpha") == "B"


def test_term_protection_prevents_numeric_rewrite(terms_path: Path):
    write_terms(
        terms_path,
        """
terms:
  - canonical: 一百米计划
    aliases: [hundred meter plan]
    protect: true
protect:
  - 三体问题
""",
    )

    assert normalize_text("hundred meter plan启动，一百米") == "一百米计划启动，100m"
    assert normalize_text("三体问题有三个变量") == "三体问题有3个变量"


def test_native_hotwords_accept_explicit_values_and_filter_invalid(terms_path: Path):
    write_terms(
        terms_path,
        """
terms:
  - canonical: Ghostty
    aliases: [鬼斯提]
    hotword: true
  - canonical: README.md
    hotwords: [README, README, too long hotword]
  - canonical: VeryLongCanonical
    hotword: true
""",
    )

    assert term_lexicon.build_native_hotword_string("VoCoType Ghostty") == (
        "Ghostty README VoCoType"
    )


def test_legacy_geequlim_dictionary_format_is_supported(terms_path: Path):
    write_terms(
        terms_path,
        """
replace:
  Ghostty: [鬼斯提, 格斯提]
protect:
  - 一加手机
""",
    )

    assert normalize_text("鬼斯提和一加手机") == "Ghostty和一加手机"


def test_terms_reload_and_keep_previous_version_on_invalid_yaml(terms_path: Path):
    write_terms(
        terms_path,
        """
terms:
  - canonical: NodeJS
    aliases: [node js]
""",
    )
    assert normalize_text("node js") == "NodeJS"

    write_terms(terms_path, "terms: [\n")
    assert normalize_text("node js") == "NodeJS"

    write_terms(
        terms_path,
        """
terms:
  - canonical: Ghostty
    aliases: [鬼斯提]
""",
    )
    assert normalize_text("鬼斯提 node js") == "Ghostty node js"


def test_xdg_path_and_legacy_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv(term_lexicon.TERMS_FILE_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "vocotype"
    config_dir.mkdir()
    legacy = config_dir / term_lexicon.LEGACY_DICTIONARY_FILENAME
    legacy.write_text("replace: {}\n", encoding="utf-8")

    assert term_lexicon.get_terms_path() == legacy
    preferred = config_dir / term_lexicon.TERMS_FILENAME
    preferred.write_text("terms: []\n", encoding="utf-8")
    assert term_lexicon.get_terms_path() == preferred
