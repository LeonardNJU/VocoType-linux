import importlib.util
import io
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CHECK = ROOT / "scripts" / "check-python-runtime.py"
IBUS_INSTALLER = ROOT / "scripts" / "install-ibus.sh"
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

    assert '"$PYTHON" "$PROJECT_DIR/scripts/check-python-runtime.py"' in source
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
