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
        "librosa",
        "yaml",
        "itn.chinese.inverse_normalizer",
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
    source = IBUS_INSTALLER.read_text(encoding="utf-8")

    assert '"$PYTHON" "$PROJECT_DIR/installers/check-python-runtime.py"' in source
    assert '$PYTHON -m pip install -r $PROJECT_DIR/requirements.txt' in source
    assert "pip install -r $PROJECT_DIR/requirements.txt" not in source.replace(
        "$PYTHON -m pip install -r $PROJECT_DIR/requirements.txt",
        "",
    )


def test_package_manifests_pin_torch_free_funasr_onnx_release():
    requirements = REQUIREMENTS.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert "funasr_onnx==0.4.2" in requirements
    assert '"funasr_onnx==0.4.2"' in pyproject
    assert "funasr_onnx==0.4.1" not in requirements
    assert "funasr_onnx==0.4.1" not in pyproject


def test_installer_does_not_offer_obsolete_funasr_onnx_torch_workaround():
    source = IBUS_INSTALLER.read_text(encoding="utf-8")

    assert "funasr_onnx 0.4.1" not in source
    assert "download.pytorch.org/whl/cpu" not in source


def test_shared_runtime_helpers_are_sourced_once_and_keep_behavior(tmp_path: Path):
    library = ROOT / "installers/runtime-common.sh"
    source = library.read_text(encoding="utf-8")
    assert source.count("get_python_version()") == 1
    assert source.count("write_slm_config_json()") == 1
    for relative in ("ibus/scripts/install.sh", "fcitx5/scripts/install.sh"):
        installer = (ROOT / relative).read_text(encoding="utf-8")
        assert 'source "$PROJECT_DIR/installers/runtime-common.sh"' in installer
        assert "get_python_version()" not in installer
        assert "write_slm_config_json()" not in installer

    version = subprocess.run(
        ["bash", "-c", f'source "{library}"; get_python_version "{sys.executable}"'],
        text=True,
        capture_output=True,
        check=False,
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == f"{sys.version_info.major}.{sys.version_info.minor}"

    config = tmp_path / "runtime.json"
    command = (
        f'source "{library}"; '
        f'write_slm_config_json "{config}" "{sys.executable}" '
        '1 remote "https://api.example/v1/chat/completions" model local-model "" '
        '12000 4 256 30000 0 secret'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["slm"]["enabled"] is True
    assert payload["slm"]["provider"] == "remote"
    assert payload["slm"]["endpoint"] == "https://api.example/v1/chat/completions"
    assert payload["slm"]["api_key"] == "secret"


def test_native_streaming_bundle_helper_is_shared_and_installs_private_runtime(tmp_path: Path):
    library = ROOT / "installers/runtime-common.sh"
    source = library.read_text(encoding="utf-8")
    assert source.count("install_native_streaming_bundle()") == 1
    for relative in ("ibus/scripts/install.sh", "fcitx5/scripts/install.sh"):
        installer = (ROOT / relative).read_text(encoding="utf-8")
        assert 'install_native_streaming_bundle "$PROJECT_DIR"' in installer

    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "lib").mkdir()
    worker = bundle / "bin/vocotype-streaming-worker"
    worker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    worker.chmod(0o755)
    (bundle / "lib/libfunasr.so").write_bytes(b"local-runtime")
    home = tmp_path / "home"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "VOCOTYPE_STREAMING_BUNDLE_DIR": str(bundle),
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{library}"; install_native_streaming_bundle "{ROOT}"',
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    installed = home / ".local/lib/vocotype-streaming"
    assert (installed / "bin/vocotype-streaming-worker").stat().st_mode & 0o100
    assert (installed / "lib/libfunasr.so").read_bytes() == b"local-runtime"
