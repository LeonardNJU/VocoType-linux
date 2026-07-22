import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CHECK = ROOT / "installers" / "check-python-runtime.py"
IBUS_INSTALLER = ROOT / "ibus" / "scripts" / "install.sh"
REQUIREMENTS = ROOT / "requirements.txt"
PYPROJECT = ROOT / "pyproject.toml"


def load_runtime_check():
    spec = importlib.util.spec_from_file_location(
        "vocotype_runtime_check",
        RUNTIME_CHECK,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_check_loads_the_actual_asr_entry_points():
    module = load_runtime_check()
    imported = []

    assert module.check_runtime(importer=imported.append) == 0
    assert imported == [
        "numpy",
        "sounddevice",
        "soundfile",
        "scipy.signal",
        "yaml",
        "modelscope.hub.snapshot_download",
        "funasr_onnx.paraformer_bin",
        "funasr_onnx.vad_bin",
        "funasr_onnx.punc_bin",
    ]


def test_runtime_check_reports_nested_dependency_failure():
    module = load_runtime_check()
    stderr = io.StringIO()

    def importer(module_name):
        if module_name == "funasr_onnx.paraformer_bin":
            raise ModuleNotFoundError("No module named 'torch'", name="torch")

    assert module.check_runtime(importer=importer, stderr=stderr) == 1
    message = stderr.getvalue()
    assert "funasr_onnx.paraformer_bin" in message
    assert "No module named 'torch'" in message


def test_ibus_system_python_uses_runtime_check_and_bound_pip_command():
    wrapper = IBUS_INSTALLER.read_text(encoding="utf-8")
    native = (ROOT / "installers/install-native-user.sh").read_text(encoding="utf-8")
    assert "install-native-user.sh" in wrapper
    assert "vocotype-model-manager" in native
    assert "verify_models" in native
    assert "pip install" not in native
    assert "python_choice" not in native.lower()



def test_package_manifests_pin_torch_free_funasr_onnx_release():
    requirements = REQUIREMENTS.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert "funasr-onnx==0.4.2" in requirements
    assert '"funasr-onnx==0.4.2"' in pyproject
    assert "funasr-onnx==0.4.1" not in requirements
    assert "funasr-onnx==0.4.1" not in pyproject
    assert "numpy==1.26.4" in requirements
    assert '"numpy==1.26.4"' in pyproject
    assert "scipy==1.16.3" in requirements
    assert '"scipy==1.16.3"' in pyproject
    assert "librosa==" not in requirements
    assert '"librosa' not in pyproject


def test_installer_does_not_offer_obsolete_funasr_onnx_torch_workaround():
    source = IBUS_INSTALLER.read_text(encoding="utf-8")

    assert "funasr_onnx 0.4.1" not in source
    assert "download.pytorch.org/whl/cpu" not in source



def test_native_installer_uses_an_exact_user_fcitx_module_stem():
    installer = (ROOT / "installers/install-native-user.sh").read_text(encoding="utf-8")
    assert 'Library=$HOME/.local/lib/fcitx5/vocotype' in installer
    assert 'vocotype.so' in installer
    assert 'FCITX_ADDON_DIRS' in installer
    assert 'app-org.fcitx.Fcitx5@autostart.service' in installer
    assert 'manager_user restart "$unit"' in installer
    assert '[[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]' in installer
    assert 'DBUS_SESSION_BUS_ADDRESS:-}" || -n "${DISPLAY' not in installer
    assert 'escape_sed_replacement' not in installer


def test_shared_runtime_helpers_are_sourced_once_and_keep_behavior(tmp_path: Path):
    for relative, framework in (("ibus/scripts/install.sh", "ibus"), ("fcitx5/scripts/install.sh", "fcitx5")):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "install-native-user.sh" in source
        assert f"--framework {framework}" in source
        assert "runtime-common.sh" not in source
        assert "python" not in source.lower()
    native = (ROOT / "installers/install-native-user.sh").read_text(encoding="utf-8")
    assert "write_default_config" in native
    assert "write_terms_template" in native
    assert "cleanup_legacy_python" in native



def test_native_streaming_bundle_helper_is_shared_and_installs_private_runtime(tmp_path: Path):
    source = (ROOT / "installers/install-native-user.sh").read_text(encoding="utf-8")
    assert "native/streaming_worker/build/bundle" in source
    assert 'cp -a "$PROJECT_DIR/native/streaming_worker/build/bundle/." "$STREAMING_HOME/"' in source
    assert "vocotype-audio-recorder" in source
    assert "vocotype-model-manager" in source
    assert "vocotype-settings" in source
