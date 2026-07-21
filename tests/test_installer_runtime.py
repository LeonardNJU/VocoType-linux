import importlib.util
import io
import json
import os
import re
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
    assert 'install_runtime_requirements "$PYTHON" "$PROJECT_DIR"' in source
    shared = Path("installers/runtime-common.sh").read_text(encoding="utf-8")
    assert "--only-binary=:all:" in shared
    assert '--no-index --find-links "$wheelhouse"' in shared
    assert "拒绝在本机编译依赖" in shared
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



def test_source_installers_escape_sed_replacement_metacharacters():
    expected = r"a\\b\&c\|d"
    for relative in ("ibus/scripts/install.sh", "fcitx5/scripts/install.sh"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        match = re.search(
            r"^escape_sed_replacement\(\) \{\n(?:    .*\n)+?\}",
            source,
            re.MULTILINE,
        )
        assert match is not None
        result = subprocess.run(
            [
                "bash",
                "-c",
                match.group(0) + '\nescape_sed_replacement "$1"',
                "bash",
                r"a\b&c|d",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected


def test_shared_runtime_helpers_are_sourced_once_and_keep_behavior(tmp_path: Path):
    library = ROOT / "installers/runtime-common.sh"
    source = library.read_text(encoding="utf-8")
    for function in (
        "get_python_version",
        "resolve_python_cmd",
        "is_supported_python",
        "detect_system_python",
        "write_slm_config_json",
    ):
        assert source.count(f"{function}()") == 1
    for relative in ("ibus/scripts/install.sh", "fcitx5/scripts/install.sh"):
        installer = (ROOT / relative).read_text(encoding="utf-8")
        assert 'source "$PROJECT_DIR/installers/runtime-common.sh"' in installer
        for function in (
            "get_python_version",
            "resolve_python_cmd",
            "is_supported_python",
            "detect_system_python",
            "write_slm_config_json",
        ):
            assert f"{function}()" not in installer

    version = subprocess.run(
        ["bash", "-c", f'source "{library}"; get_python_version "{sys.executable}"'],
        text=True,
        capture_output=True,
        check=False,
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == f"{sys.version_info.major}.{sys.version_info.minor}"

    resolved = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{library}"; resolve_python_cmd "{sys.executable}"',
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    assert Path(resolved.stdout.strip()).resolve() == Path(sys.executable).resolve()

    supported = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{library}"; PYTHON_MIN_MINOR=11; PYTHON_MAX_MINOR=12; '
            f'is_supported_python "{sys.executable}"',
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert supported.returncode == 0, supported.stderr

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3.12").symlink_to(Path(sys.executable).resolve())
    detected = subprocess.run(
        [
            "bash",
            "-c",
            f'PATH="{bin_dir}"; source "{library}"; '
            'PYTHON_MIN_MINOR=11; PYTHON_MAX_MINOR=12; detect_system_python',
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert detected.returncode == 0, detected.stderr
    assert Path(detected.stdout.strip()).resolve() == Path(sys.executable).resolve()

    config = tmp_path / "runtime.json"
    command = (
        f'source "{library}"; '
        f'write_slm_config_json "{config}" "{sys.executable}" '
        '1 "https://api.example/v1/chat/completions" model '
        '12000 4 256 0 secret'
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
    assert "provider" not in payload["slm"]
    assert payload["slm"]["endpoint"] == "https://api.example/v1/chat/completions"
    assert payload["slm"]["api_key"] == "secret"
    assert payload["slm"]["remote_stream"] is True
    for obsolete in ("local_model", "local_python", "warmup_timeout_ms"):
        assert obsolete not in payload["slm"]


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
