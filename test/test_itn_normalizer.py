from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from app import itn_normalizer, term_lexicon
from app.itn_normalizer import normalize_mandatory_itn
from app.text_normalizer import normalize_text


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def reset_itn():
    itn_normalizer._reset_itn_for_tests()
    yield
    itn_normalizer._reset_itn_for_tests()


def test_mandatory_itn_converts_unhandled_plain_quantity():
    assert normalize_mandatory_itn("系统还有二百五十六台机器") == (
        "系统还有256台机器"
    )


def test_mandatory_itn_rejects_date_unit_and_time_restyling():
    assert normalize_mandatory_itn("二零二六年五月十一号") == "二零二六年五月十一号"
    assert normalize_mandatory_itn("下午三点二十分开会") == "下午三点二十分开会"
    assert normalize_mandatory_itn("跑了三百二十米") == "跑了三百二十米"


def test_product_numeric_output_is_masked_from_fst_restyling():
    assert normalize_mandatory_itn(
        "延迟1.5秒",
        source_text="延迟一点五秒",
    ) == "延迟1.5秒"
    assert normalize_text("下午三点二十分开会") == "下午3点20分开会"
    assert normalize_text("二零二六年五月十一号") == "2026年5月11号"


def test_fixed_phrase_mask_does_not_split_larger_number():
    assert normalize_mandatory_itn(
        "三十而立",
        fixed_phrases={"三十而立"},
    ) == "三十而立"
    assert normalize_mandatory_itn(
        "二百五十六",
        fixed_phrases={"二百五"},
    ) == "256"


def test_term_protection_survives_product_policy_and_itn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    terms = tmp_path / "terms.yaml"
    terms.write_text(
        """
terms:
  - canonical: 一百米计划
    aliases: [hundred meter plan]
    protect: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(term_lexicon.TERMS_FILE_ENV, str(terms))
    term_lexicon._reset_term_lexicon_cache()

    assert normalize_text("hundred meter plan有二百五十六台机器") == (
        "一百米计划有256台机器"
    )
    term_lexicon._reset_term_lexicon_cache()


def test_itn_dependency_is_mandatory_and_has_no_config_switch():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert "WeTextProcessing==1.2.0" in dependencies
    assert "WeTextProcessing==1.2.0" in (
        ROOT / "requirements.txt"
    ).read_text(encoding="utf-8")

    config_source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    server_source = (ROOT / "app" / "funasr_server.py").read_text(
        encoding="utf-8"
    )
    assert "normalize_chinese_numbers" not in config_source
    assert 'default_options["normalize_chinese_numbers"]' not in server_source
    assert "normalize_text(final_text)" in server_source
