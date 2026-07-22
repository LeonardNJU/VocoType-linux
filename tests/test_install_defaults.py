from pathlib import Path
import tomllib

from app.config import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
COMMON_RUNTIME = ROOT / "installers" / "runtime-common.sh"
INSTALLERS = (
    ROOT / "ibus" / "scripts" / "install.sh",
    ROOT / "fcitx5" / "scripts" / "install.sh",
)


def test_pygobject_is_built_in_ci_for_each_supported_distro():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert "PyGObject>=3.46" in dependencies

    ubuntu_constraint = (
        ROOT / "packaging/constraints/ubuntu-ci.txt"
    ).read_text(encoding="utf-8")
    assert "PyGObject==3.50.2" in ubuntu_constraint

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--constraint packaging/constraints/ubuntu-ci.txt" in ci
    assert "python -m pip install -e ." not in ci

    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "PyGObject==3.50.2" in release
    assert release.count("PyGObject==3.56.3") >= 2
    assert "build-runtime-wheelhouse.sh" in release


def test_ibus_release_installers_check_runtime_not_build_dependencies():
    gui = (ROOT / "ibus" / "scripts" / "install-gui.sh").read_text(encoding="utf-8")
    cli = (ROOT / "ibus" / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "from gi.repository import Gtk, IBus" in gui
    assert "check_runtime_deps" in cli
    for source in (gui, cli):
        assert "check_build_deps" not in source
        assert "libgirepository1.0-dev" not in source


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
    shared = COMMON_RUNTIME.read_text(encoding="utf-8")
    for obsolete in (
        "local_model",
        "local_python",
        "warmup_timeout_ms",
        "keepalive_ms",
        "ready_wait_ms",
    ):
        assert f'"{obsolete}"' in shared  # migration cleanup only
    assert 'slm["endpoint"]' not in shared
    assert '"endpoint": endpoint' in shared
    for path in INSTALLERS:
        script = path.read_text(encoding="utf-8")
        assert "OpenAI-compatible API" in script
        assert "不启动或管理模型进程" in script
        assert "torch" not in script
        assert "transformers" not in script
        assert "local_ephemeral" not in script

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
    shared = COMMON_RUNTIME.read_text(encoding="utf-8")
    assert '"remote_stream": True' in shared
    assert '"stream_idle_timeout_ms": timeout_ms' in shared
    assert '"remote_max_tokens": int(slm.get("remote_max_tokens", 0) or 0)' in shared
    assert '"extra_headers": slm.get("extra_headers", {})' in shared


def test_official_two_pass_preview_is_optional_and_cpu_bounded_by_default():
    streaming = DEFAULT_CONFIG["asr_streaming"]
    assert streaming["enabled"] is False
    assert streaming["chunk_size"] == [5, 10, 5]
    assert streaming["intra_op_num_threads"] == 1
    assert streaming["idle_timeout_s"] == 30
    assert streaming["session_idle_timeout_s"] == 15
    assert streaming["model"].endswith("-online-onnx")
