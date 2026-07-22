from pathlib import Path
import tomllib

from app.config import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = (
    ROOT / "ibus" / "scripts" / "install.sh",
    ROOT / "fcitx5" / "scripts" / "install.sh",
)


def test_native_packages_do_not_build_or_ship_pygobject_wheelhouses():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    # The retained Python development package may still describe legacy tools;
    # native package/release jobs must not build that dependency closure.
    assert "project" in project
    for workflow_name in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        assert "build-runtime-wheelhouse.sh" not in workflow
        assert "wheelhouse-fcitx5" not in workflow
        assert "wheelhouse-ibus" not in workflow
        assert "VOCOTYPE_WHEELHOUSE_DIR" not in workflow
        assert "PyGObject==" not in workflow
    stage = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    assert "runtime=native" in stage
    assert "vendor/wheelhouse" not in stage



def test_ibus_release_installers_check_runtime_not_build_dependencies():
    for path in INSTALLERS:
        source = path.read_text(encoding="utf-8")
        assert "install-native-user.sh" in source
        assert "python" not in source.lower()
    helper = Path("installers/install-system-dependencies.sh").read_text(encoding="utf-8")
    assert "libibus-1.0-dev" in helper
    assert "librime-dev" in helper



def test_default_remote_slm_budget_is_not_the_legacy_600ms_24_tokens():
    slm = DEFAULT_CONFIG["slm"]
    assert "provider" not in slm
    assert slm["timeout_ms"] == 20000
    assert slm["remote_stream"] is True
    assert slm["stream_idle_timeout_ms"] == 20000
    assert slm["transport_timeout_ms"] == 0
    assert slm["remote_max_tokens"] == 0
    assert slm["max_tokens"] == 128


def test_installers_only_offer_openai_compatible_api():
    source = Path("installers/install-native-user.sh").read_text(encoding="utf-8")
    assert "OpenAI-compatible" not in source or "chat/completions" in source
    assert "chat/completions" in source
    assert "local_model" not in source
    assert "local_python" not in source


def test_default_asr_model_is_contextual_onnx_with_native_hotword_support():
    from app.funasr_config import MODELS

    assert "contextual" in MODELS["asr"]["name"]
    assert MODELS["asr"]["name"].endswith("-onnx")


def test_installers_create_shared_terms_template_without_overwriting_legacy_file():
    source = Path("installers/install-native-user.sh").read_text(encoding="utf-8")
    assert 'local path="$CONFIG_DIR/terms.yaml"' in source
    assert '[[ -f "$path" ]] && return 0' in source
    assert "canonical: VoCoType" in source
    assert "hotword: true" in source



def test_installers_write_remote_streaming_defaults():
    installer = (ROOT / "installers/install-native-user.sh").read_text(encoding="utf-8")
    assert '"remote_stream": true' in installer
    assert '"enable_thinking": false' in installer
    assert '"timeout_ms": 20000' in installer
    assert '"max_tokens": 128' in installer
    assert '"edit_enabled": true' in installer



def test_official_two_pass_preview_is_optional_and_cpu_bounded_by_default():
    streaming = DEFAULT_CONFIG["asr_streaming"]
    assert streaming["enabled"] is False
    assert streaming["chunk_size"] == [5, 10, 5]
    assert streaming["intra_op_num_threads"] == 1
    assert streaming["idle_timeout_s"] == 30
    assert streaming["session_idle_timeout_s"] == 15
    assert streaming["model"].endswith("-online-onnx")
