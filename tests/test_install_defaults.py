from pathlib import Path
import tomllib

from app.config import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = (
    ROOT / "ibus" / "scripts" / "install.sh",
    ROOT / "fcitx5" / "scripts" / "install.sh",
)


def test_pygobject_constraint_supports_ubuntu_22_04_and_python_3_12():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert "PyGObject>=3.46,<3.52" in dependencies
    assert not any(
        dependency.startswith("PyGObject") and ";" in dependency
        for dependency in dependencies
    )


def test_ibus_installer_checks_the_pkg_config_name_used_by_pygobject_3_50():
    script = (ROOT / "ibus" / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "pkg-config --exists gobject-introspection-1.0" in script
    assert 'missing="$missing libgirepository1.0-dev"' in script


def test_default_remote_slm_budget_is_not_the_legacy_600ms_24_tokens():
    slm = DEFAULT_CONFIG["slm"]
    assert slm["provider"] == "remote"
    assert slm["timeout_ms"] == 20000
    assert slm["remote_stream"] is True
    assert slm["stream_idle_timeout_ms"] == 20000
    assert slm["transport_timeout_ms"] == 0
    assert slm["remote_max_tokens"] == 0
    assert slm["max_tokens"] == 128


def test_installers_omit_endpoint_for_local_provider_and_use_provider_defaults():
    for path in INSTALLERS:
        script = path.read_text(encoding="utf-8")
        assert 'slm.pop("endpoint", None)' in script
        assert "SLM_TIMEOUT_MS=12000" in script
        assert "SLM_WARMUP_TIMEOUT_MS=90000" in script
        assert "SLM_MAX_TOKENS=96" in script
        assert 'SLM_PROVIDER="remote"\n            SLM_TIMEOUT_MS=20000' in script
        assert "SLM_MAX_TOKENS=128" in script


def test_default_asr_model_is_contextual_onnx_with_native_hotword_support():
    from app.funasr_config import MODELS

    assert "contextual" in MODELS["asr"]["name"]
    assert MODELS["asr"]["name"].endswith("-onnx")


def test_installers_create_shared_terms_template_without_overwriting_legacy_file():
    for path in INSTALLERS:
        script = path.read_text(encoding="utf-8")
        assert 'TERMS_FILE="$TERMS_DIR/terms.yaml"' in script
        assert 'LEGACY_TERMS_FILE="$TERMS_DIR/user-dictionary.yaml"' in script
        assert 'cp "$PROJECT_DIR/data/terms.yaml" "$TERMS_FILE"' in script
        assert '[ ! -e "$TERMS_FILE" ] && [ ! -e "$LEGACY_TERMS_FILE" ]' in script


def test_installers_write_remote_streaming_defaults():
    for path in INSTALLERS:
        script = path.read_text(encoding="utf-8")
        assert 'slm["remote_stream"] = True' in script
        assert 'slm["stream_idle_timeout_ms"] = timeout_ms' in script
        assert 'slm.setdefault("remote_max_tokens", 0)' in script
        assert 'slm.setdefault("extra_headers", {})' in script
