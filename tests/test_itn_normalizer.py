from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from app import term_lexicon
from app.text_normalizer import normalize_text


ROOT = Path(__file__).resolve().parents[1]


def test_deterministic_rules_convert_plain_quantity_without_fst():
    assert normalize_text("系统还有二百五十六台机器") == "系统还有256台机器"


@pytest.mark.parametrize(
    ("spoken", "written"),
    [
        ("二百五十六台机器", "256台机器"),
        ("十二盒药", "12盒药"),
        ("三十七件任务", "37件任务"),
        ("八个用户", "8个用户"),
    ],
)
def test_guarded_count_classifiers_are_covered(spoken: str, written: str):
    assert normalize_text(spoken) == written


def test_product_numeric_policy_controls_date_time_distance_and_currency():
    assert normalize_text("下午三点二十分开会") == "15:20开会"
    assert normalize_text("二零二六年五月十一号") == "2026/05/11"
    assert normalize_text("跑了三百二十米") == "跑了320m"
    assert normalize_text("价格是二百五十六元") == "价格是¥256"
    assert normalize_text(
        "下午三点二十分开会",
        config={"compact_times": False},
    ) == "下午3点20分开会"
    assert normalize_text(
        "二零二六年五月十一号",
        config={"compact_dates": False},
    ) == "2026年5月11号"


def test_fixed_phrases_remain_protected_while_larger_numbers_convert():
    assert normalize_text("三十而立") == "三十而立"
    assert normalize_text("二百五十六") == "256"


def test_term_protection_survives_deterministic_numeric_policy(
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


def test_heavy_fst_dependency_is_removed_and_runtime_remains_configurable():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    for obsolete in ("WeTextProcessing", "pynini"):
        assert all(obsolete.casefold() not in item.casefold() for item in dependencies)
        assert obsolete.casefold() not in requirements.casefold()
        assert f'name = "{obsolete.casefold()}"' not in lock.casefold()

    config_source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    server_source = (ROOT / "app" / "funasr_server.py").read_text(encoding="utf-8")
    assert '"normalization"' in config_source
    assert 'default_options.get("normalization")' in server_source
    assert normalize_text("二百五十六台", config={"enabled": False}) == "二百五十六台"
