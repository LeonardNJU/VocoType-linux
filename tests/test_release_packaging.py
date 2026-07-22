from __future__ import annotations

import importlib.util
import io
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from settings_center.setup_manager import installation_paths

ROOT = Path(__file__).resolve().parents[1]


def _version() -> str:
    namespace: dict[str, object] = {}
    exec((ROOT / "vocotype_version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["__version__"])


def _version_field(field: str, value: str | None = None) -> str:
    return subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "packaging/tools/versioning.py"),
            value or _version(),
            "--field",
            field,
        ],
        text=True,
    ).strip()


def _render_package_metadata(
    format_name: str,
    flavor: str,
    tmp_path: Path,
) -> str:
    templates = {
        "debian": ROOT / "packaging/debian/control",
        "rpm": ROOT / "packaging/rpm/vocotype.spec.in",
        "arch": ROOT / "packaging/arch/PKGBUILD.in",
    }
    output = tmp_path / f"{format_name}-{flavor}.txt"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging/tools/render-package-metadata.py"),
            "--format",
            format_name,
            "--flavor",
            flavor,
            "--template",
            str(templates[format_name]),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_text(encoding="utf-8")


def _release_entries() -> list[str]:
    entries: list[str] = []
    for raw in (ROOT / "packaging/manifests/runtime-files.txt").read_text(encoding="utf-8").splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            entries.append(value)
    return entries


def _run(*argv: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        **kwargs,
    )


def _write_minimal_wheel(path: Path, distribution: str, version: str) -> None:
    dist_info = f"{distribution.replace('-', '_')}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n"
    wheel = "Wheel-Version: 1.0\nGenerator: vocotype-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(f"{dist_info}/WHEEL", wheel)
        archive.writestr(f"{dist_info}/RECORD", "")



def test_repository_layout_groups_tools_by_responsibility():
    assert not (ROOT / "scripts").exists()
    assert not (ROOT / "test").exists()
    for relative in (
        "ibus/scripts/install.sh",
        "ibus/scripts/install-gui.sh",
        "ibus/scripts/uninstall.sh",
        "ibus/scripts/uninstall-gui.sh",
        "fcitx5/scripts/install-gui.sh",
        "fcitx5/scripts/uninstall.sh",
        "fcitx5/scripts/uninstall-gui.sh",
        "installers/uninstall-integration.sh",
        "fcitx5/scripts/install.sh",
        "installers/check-python-runtime.py",
        "packaging/tools/build-release.py",
        "packaging/tests/smoke-installed-package.sh",
        "packaging/tests/smoke-ibus-registry.sh",
        "tools/diagnostics/analyze-rime-logs.sh",
        "tools/diagnostics/validate-ibus-install.py",
        "tools/benchmarks/slm-pipeline.py",
        "docs/guides/settings-center.md",
        "docs/troubleshooting/faq.md",
    ):
        assert (ROOT / relative).is_file(), relative


def test_live_ibus_install_validation_is_a_diagnostic_not_a_pytest_case():
    assert not (ROOT / "tests/test_ibus_install.py").exists()
    validator = ROOT / "tools/diagnostics/validate-ibus-install.py"
    assert validator.is_file()
    source = validator.read_text(encoding="utf-8")
    assert "VOCOTYPE_VALIDATE_INSTALL" not in source
    assert "pytest.skip" not in source
    assert "Path.home()" in source

def test_release_manifest_is_safe_complete_and_unique():
    entries = _release_entries()
    assert entries
    assert len(entries) == len(set(entries))
    required = {
        "app",
        "settings_center",
        "ibus",
        "fcitx5/backend",
        "fcitx5/common",
        "fcitx5/data",
        "fcitx5/module",
        "fcitx5/scripts",
        "installers",
        "native/streaming_worker",
        "data",
        "docs",
        "tools/diagnostics/validate-ibus-install.py",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        "vocotype_version.py",
        "LICENSE",
    }
    assert required.issubset(entries)
    entry_paths = [Path(entry) for entry in entries]
    for index, path in enumerate(entry_paths):
        for other in entry_paths[index + 1 :]:
            assert path not in other.parents, f"redundant manifest entry: {path} contains {other}"
            assert other not in path.parents, f"redundant manifest entry: {other} contains {path}"
    for entry in entries:
        path = Path(entry)
        assert not path.is_absolute()
        assert ".." not in path.parts
        assert (ROOT / path).exists(), entry


def test_staging_script_rejects_root_destination():
    result = _run("bash", "packaging/tools/stage-system-package.sh", "--destdir", "/", "--skip-module-build")
    assert result.returncode == 2
    assert "Refusing" in result.stderr


def test_staging_script_builds_complete_noninteractive_tree(tmp_path: Path):
    dest = tmp_path / "root"
    result = _run(
        "bash",
        "packaging/tools/stage-system-package.sh",
        "--destdir",
        str(dest),
        "--skip-module-build",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source_root = dest / "usr/share/vocotype"
    marker = (source_root / ".system-package").read_text(encoding="utf-8")
    assert f"version={_version()}" in marker
    assert "managed-by=native-package" in marker
    assert any(f"manager={name}" in marker for name in ("apt", "dnf", "pacman"))
    assert "source=" not in marker
    assert str(ROOT) not in marker
    for entry in _release_entries():
        assert (source_root / entry).exists(), entry
    expected = [
        dest / "usr/bin/vocotype-settings",
        dest / "usr/bin/vocotype-fcitx5-backend",
        dest / "usr/bin/vocotype-fcitx5-recorder",
        dest / "usr/libexec/vocotype-ibus-engine",
        dest / "usr/share/fcitx5/addon/vocotype.conf",
        dest / "usr/share/ibus/component/vocotype.xml",
        dest / "usr/lib/systemd/user/vocotype-fcitx5-backend.service",
        dest / "usr/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop",
        dest / "usr/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml",
        dest / "usr/share/icons/hicolor/192x192/apps/vocotype.png",
    ]
    for path in expected:
        assert path.exists(), path
    for path in expected[:4]:
        assert path.stat().st_mode & stat.S_IXUSR
    xml = (dest / "usr/share/ibus/component/vocotype.xml").read_text(encoding="utf-8")
    assert f"<version>{_version()}</version>" in xml
    assert "<exec>/usr/libexec/vocotype-ibus-engine --ibus</exec>" in xml
    assert "VOCOTYPE_" not in xml
    assert not list(source_root.rglob("*.pyc"))
    assert not list(source_root.rglob("__pycache__"))
    assert (source_root / "packaging/tools/audit-wheelhouse.py").is_file()
    packaged_tools = sorted(
        path.relative_to(source_root).as_posix()
        for path in (source_root / "packaging").rglob("*")
        if path.is_file()
    )
    assert packaged_tools == ["packaging/tools/audit-wheelhouse.py"]
    for excluded in (
        ".github",
        "test",
        "packaging/tools/build-release.py",
        "packaging/tools/validate-release.py",
        "packaging/tools/build-deb.sh",
        "packaging/tools/build-rpm.sh",
        "packaging/tools/build-arch.sh",
    ):
        assert not (source_root / excluded).exists(), excluded



def test_staging_script_places_prebuilt_native_streaming_bundle(tmp_path: Path):
    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "lib").mkdir()
    worker = bundle / "bin/vocotype-streaming-worker"
    worker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    worker.chmod(0o755)
    (bundle / "lib/libfunasr.so").write_bytes(b"native")
    dest = tmp_path / "root"
    env = os.environ.copy()
    env.update(
        {
            "VOCOTYPE_STREAMING_BUNDLE_DIR": str(bundle),
            "VOCOTYPE_REQUIRE_STREAMING_BUNDLE": "1",
        }
    )
    result = _run(
        "bash",
        "packaging/tools/stage-system-package.sh",
        "--destdir",
        str(dest),
        "--skip-module-build",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    installed_worker = dest / "usr/libexec/vocotype-streaming-worker"
    private_worker = dest / "usr/lib/vocotype/vocotype-streaming-worker"
    assert installed_worker.is_file()
    assert not installed_worker.is_symlink()
    assert installed_worker.stat().st_mode & stat.S_IXUSR
    launcher_text = installed_worker.read_text(encoding="utf-8")
    assert 'exec /usr/lib/vocotype/vocotype-streaming-worker "$@"' in launcher_text
    assert private_worker.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert (dest / "usr/lib/vocotype/libfunasr.so").read_bytes() == b"native"

def test_staging_script_honors_custom_libexec_directory(tmp_path: Path):
    dest = tmp_path / "root"
    result = _run(
        "bash",
        "packaging/tools/stage-system-package.sh",
        "--destdir",
        str(dest),
        "--libexecdir",
        "/usr/lib/vocotype",
        "--skip-module-build",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    launcher = dest / "usr/lib/vocotype/vocotype-ibus-engine"
    assert launcher.is_file()
    assert launcher.stat().st_mode & stat.S_IXUSR
    assert not (dest / "usr/libexec/vocotype-ibus-engine").exists()
    xml = (dest / "usr/share/ibus/component/vocotype.xml").read_text(encoding="utf-8")
    assert "<exec>/usr/lib/vocotype/vocotype-ibus-engine --ibus</exec>" in xml


def test_staging_script_rejects_relative_libexec_directory(tmp_path: Path):
    result = _run(
        "bash",
        "packaging/tools/stage-system-package.sh",
        "--destdir",
        str(tmp_path / "root"),
        "--libexecdir",
        "lib/vocotype",
        "--skip-module-build",
    )
    assert result.returncode == 2
    assert "--libexecdir must be absolute" in result.stderr


def test_staging_script_honors_multilib_directory(tmp_path: Path):
    dest = tmp_path / "root"
    build = tmp_path / "build"
    result = _run(
        "bash",
        "packaging/tools/stage-system-package.sh",
        "--destdir",
        str(dest),
        "--libdir",
        "lib/test-multiarch",
        "--build-dir",
        str(build),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    module = dest / "usr/lib/test-multiarch/fcitx5/vocotype.so"
    assert module.is_file()
    assert module.stat().st_size > 0


def test_system_launchers_never_install_dependencies_or_request_privilege():
    for path in (ROOT / "packaging/bin").iterdir():
        source = path.read_text(encoding="utf-8")
        assert "sudo" not in source
        assert "pkexec" not in source
        assert "pip install" not in source
        assert "curl " not in source
        assert ".local/share/vocotype" in source
        assert "export PYTHONDONTWRITEBYTECODE=1" in source


def _write_fake_settings_python(
    path: Path,
    *,
    probe_ok: bool,
    receipt: Path | None = None,
) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'if [[ "${1:-}" == "-c" ]]; then',
        f"  exit {0 if probe_ok else 1}",
        "fi",
    ]
    if receipt is not None:
        lines += [
            f"printf '%s\\n' \"$@\" > {str(receipt)!r}",
            f"printf '%s\\n' \"${{VOCOTYPE_PROJECT_DIR:-}}\" >> {str(receipt)!r}",
            f"printf '%s\\n' \"${{PYTHONPATH:-}}\" >> {str(receipt)!r}",
        ]
    lines.append("exit 0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def test_settings_launcher_prefers_distro_python_with_complete_gtk_runtime(
    tmp_path: Path,
):
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    receipt = tmp_path / "receipt.txt"
    _write_fake_settings_python(fake_bin / "python3", probe_ok=True, receipt=receipt)
    _write_fake_settings_python(fake_bin / "python3.12", probe_ok=False)

    private_python = home / ".local/share/vocotype/.venv/bin/python"
    private_python.parent.mkdir(parents=True)
    _write_fake_settings_python(private_python, probe_ok=False)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "VOCOTYPE_SYSTEM_ROOT": "/opt/vocotype-test",
            "PYTHONPATH": "tail",
        }
    )
    result = subprocess.run(
        [str(ROOT / "packaging/bin/vocotype-settings"), "--example"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = receipt.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["-m", "settings_center.application", "--example"]
    assert lines[3] == "/opt/vocotype-test"
    assert lines[4].startswith("/opt/vocotype-test")


def test_settings_launcher_falls_back_to_compatible_user_runtime(tmp_path: Path):
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_settings_python(fake_bin / "python3", probe_ok=False)

    receipt = tmp_path / "receipt.txt"
    private_python = home / ".local/share/vocotype/.venv/bin/python"
    private_python.parent.mkdir(parents=True)
    _write_fake_settings_python(private_python, probe_ok=True, receipt=receipt)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "VOCOTYPE_SYSTEM_ROOT": "/opt/vocotype-test",
        }
    )
    result = subprocess.run(
        [str(ROOT / "packaging/bin/vocotype-settings"), "--example"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert receipt.read_text(encoding="utf-8").splitlines()[:3] == [
        "-m",
        "settings_center.application",
        "--example",
    ]


def test_settings_launcher_probe_reports_selected_runtime(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_settings_python(fake_bin / "python3", probe_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "VOCOTYPE_SETTINGS_PROBE_ONLY": "1",
        }
    )
    result = subprocess.run(
        [str(ROOT / "packaging/bin/vocotype-settings")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "SETTINGS_RUNTIME_OK python3\n"


def test_backend_launcher_fails_cleanly_before_gui_setup(tmp_path: Path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "empty-home")
    result = subprocess.run(
        [str(ROOT / "packaging/bin/vocotype-fcitx5-backend")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 78
    assert "Open VoCoType Settings" in result.stderr


def test_version_is_consistent_across_package_metadata():
    version = _version()
    assert version.startswith("3.0.0")
    assert _version_field("tag") == "v3.0.0-beta.2"
    assert _version_field("debian") == "3.0.0~beta2"
    assert _version_field("rpm_version") == "3.0.0"
    assert _version_field("rpm_release") == "0.beta2"
    assert _version_field("arch") == "3.0.0b2"
    changelog = (ROOT / "packaging/debian/changelog").read_text(encoding="utf-8")
    assert changelog.startswith(
        f"vocotype-linux ({_version_field('debian')}-1)"
    )
    cmake = (ROOT / "fcitx5/module/CMakeLists.txt").read_text(encoding="utf-8")
    stage = (ROOT / "packaging/tools/stage-system-package.sh").read_text(
        encoding="utf-8"
    )
    assert "vocotype_version.py" in cmake
    assert 'CMAKE_VERSION=$(python3 "$PROJECT_DIR/packaging/tools/versioning.py"' in stage
    assert '-DVOCOTYPE_VERSION="$CMAKE_VERSION"' in stage
    assert "@VERSION@" in (ROOT / "packaging/rpm/vocotype.spec.in").read_text(encoding="utf-8")
    assert "@VERSION@" in (ROOT / "packaging/arch/PKGBUILD.in").read_text(encoding="utf-8")


def test_release_builder_rejects_tag_version_mismatch(tmp_path: Path):
    result = _run(
        sys.executable,
        "packaging/tools/build-release.py",
        "--source-only",
        "--output",
        str(tmp_path),
        "--expected-version",
        "v0.0.0",
    )
    assert result.returncode == 2
    assert "vocotype_version.py contains" in result.stderr


def test_release_builder_module_has_stable_hash_metadata_api():
    spec = importlib.util.spec_from_file_location("vocotype_build_release", ROOT / "packaging/tools/build-release.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.read_version() == _version()
    assert callable(module.build_source_archive)
    assert callable(module.write_metadata)


def test_release_builder_cleans_checkout_metadata(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "vocotype_build_release_cleanup",
        ROOT / "packaging/tools/build-release.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    egg_info = tmp_path / "vocotype_linux.egg-info"
    cache = tmp_path / "app/__pycache__"
    egg_info.mkdir()
    cache.mkdir(parents=True)
    (cache / "module.cpython-312.pyc").write_bytes(b"cache")

    module.clean_generated_source_metadata()

    assert not egg_info.exists()
    assert not cache.exists()



def test_package_metadata_renderer_rejects_placeholders_next_to_punctuation(
    tmp_path: Path,
):
    template = tmp_path / "control.in"
    output = tmp_path / "control"
    template.write_text(
        "Package: @PACKAGE_NAME@\nDescription: unresolved (@UNKNOWN_TOKEN@),\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging/tools/render-package-metadata.py"),
            "--format",
            "debian",
            "--flavor",
            "ibus",
            "--template",
            str(template),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "@UNKNOWN_TOKEN@" in result.stderr
    assert not output.exists()


def test_native_package_recipes_share_one_staging_contract(tmp_path: Path):
    recipes = {
        ROOT / "packaging/debian/rules": "apt",
        ROOT / "packaging/rpm/vocotype.spec.in": "dnf",
        ROOT / "packaging/arch/PKGBUILD.in": "pacman",
    }
    for recipe, manager in recipes.items():
        source = recipe.read_text(encoding="utf-8")
        assert "packaging/tools/stage-system-package.sh" in source
        assert "--flavor" in source
        assert f"--package-manager {manager}" in source
        assert "pip install" not in source
        assert "download_models" not in source
    for flavor, package_name in (
        ("universal", "vocotype-linux"),
        ("ibus", "vocotype-linux-ibus"),
        ("fcitx5", "vocotype-linux-fcitx5"),
    ):
        control = _render_package_metadata("debian", flavor, tmp_path)
        spec = _render_package_metadata("rpm", flavor, tmp_path)
        pkgbuild = _render_package_metadata("arch", flavor, tmp_path)
        assert f"Package: {package_name}" in control
        assert f"Name:           {package_name}" in spec
        assert f"pkgname={package_name}" in pkgbuild
        assert "Architecture: amd64" in control
        assert "License:        GPL-3.0-or-later" in spec
        assert "%global debug_package %{nil}" in spec
        assert "sha256sums=('@SOURCE_SHA256@')" in pkgbuild
        assert "options=('!debug' '!strip')" in pkgbuild
        assert "--libexecdir /usr/lib/vocotype" in pkgbuild
        assert '--libexecdir "%{_libexecdir}"' in spec
        assert "SKIP" not in pkgbuild

def test_debian_maintainer_scripts_are_noninteractive_and_offline():
    for name in ("postinst", "postrm"):
        source = (ROOT / "packaging/debian" / name).read_text(encoding="utf-8")
        for forbidden in ("read ", "/dev/tty", "curl ", "wget ", "pip ", "systemctl --user"):
            assert forbidden not in source


def test_system_package_reuse_paths_are_covered_by_installers():
    fcitx = (ROOT / "fcitx5/scripts/install.sh").read_text(encoding="utf-8")
    assert "REUSE_SYSTEM_MODULE" in fcitx
    assert "/usr/share/fcitx5/addon/vocotype.conf" in fcitx
    assert "manage-fcitx-system-integration.sh" in fcitx
    assert "跳过开发依赖检查" in fcitx
    ibus = (ROOT / "ibus/scripts/install-gui.sh").read_text(encoding="utf-8")
    assert "/usr/libexec/vocotype-ibus-engine" in ibus
    assert "/usr/lib/vocotype/vocotype-ibus-engine" in ibus
    assert 'COMPONENT_EXEC_PATH="$packaged_launcher"' in ibus
    assert 'cmp -s "$TEMP_COMPONENT" "$SYSTEM_COMPONENT_DIR/vocotype.xml"' in ibus


def test_fcitx_module_has_system_recorder_fallback():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    assert 'access("/usr/bin/vocotype-fcitx5-recorder", X_OK)' in source
    assert 'recorder_launcher_path_ = "/usr/bin/vocotype-fcitx5-recorder"' in source





def test_uninstall_restarts_are_bounded_for_headless_package_smoke():
    script = (ROOT / "installers/uninstall-integration.sh").read_text(encoding="utf-8")
    assert "VOCOTYPE_RESTART_TIMEOUT_SECONDS" in script
    assert "run_bounded_restart ibus restart" in script
    assert "run_bounded_restart env -u FCITX_ADDON_DIRS fcitx5 -r -d" in script


def test_native_package_smoke_runs_isolated_input_method_registries():
    smoke = (ROOT / "packaging/tests/smoke-installed-package.sh").read_text(encoding="utf-8")
    ibus_registry = (ROOT / "packaging/tests/smoke-ibus-registry.sh").read_text(encoding="utf-8")
    fcitx_registry = (ROOT / "packaging/tests/smoke-fcitx-addon.sh").read_text(encoding="utf-8")
    assert "smoke-ibus-registry.sh" in smoke
    assert "smoke-fcitx-addon.sh" in smoke
    assert "dbus-run-session" in ibus_registry
    assert "GIO_USE_VFS=local" in ibus_registry
    assert "ibus-daemon" in ibus_registry
    assert "IBUS_REGISTRY_SMOKE_OK" in ibus_registry
    assert "dbus-run-session" in fcitx_registry
    assert "Loaded addon vocotype" in fcitx_registry
    assert "FCITX_ADDON_LOAD_OK" in fcitx_registry

    for workflow_name in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        assert "dbus-daemon" in workflow

def test_native_package_smoke_exercises_lifecycle_ownership_boundary():
    smoke = (ROOT / "packaging/tests/smoke-installed-package.sh").read_text(encoding="utf-8")
    assert "/usr/share/vocotype/ibus/scripts/uninstall-gui.sh" in smoke
    assert "/usr/share/vocotype/fcitx5/scripts/uninstall-gui.sh" in smoke
    assert "NATIVE_PACKAGE_COMMAND:" in smoke
    assert "PACKAGE_UNINSTALL_OWNERSHIP_OK" in smoke

def test_ci_discovers_shell_scripts_by_responsibility_directory():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "find ibus/scripts fcitx5/scripts installers packaging/tools packaging/tests tools/diagnostics" in workflow
    assert "xargs -0 -n1 bash -n" in workflow
    assert "compileall -q app settings_center ibus fcitx5/backend installers packaging/tools tools tests" in workflow

def test_workflows_parse_and_pin_current_major_actions():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert ci["jobs"]["python-tests"]["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    release_text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for text in (ci_text, release_text):
        assert "actions/checkout@v6" in text
        assert "actions/upload-artifact@v7" in text
    assert "actions/setup-python@v6" in ci_text
    assert "actions/download-artifact@v5" in release_text
    assert "softprops/action-gh-release" not in release_text
    assert "gh release create" in release_text
    assert "inputs.publish == true" in release_text
    assert 'tags: ["v*"]' not in release_text
    assert release["jobs"]["assemble"]["needs"] == [
        "validate-version",
        "native-streaming",
        "source-python-deb",
        "rpm",
        "arch",
    ]
    assert release["jobs"]["publish"]["needs"] == [
        "validate-version",
        "assemble",
    ]
    assemble_text = str(release["jobs"]["assemble"])
    assert "final-release-assets" in assemble_text
    assert "validate-final-release-assets.py" in assemble_text
    assert "--installers-only" in assemble_text
    assert "SHA256SUMS" in assemble_text
    assert "release-assets.json" not in assemble_text
    assert "SHA256SUMS.all" not in assemble_text
    publish_step = release["jobs"]["publish"]["steps"][-1]
    assert publish_step["name"] == "Create tag and Release from the tested assets"
    publish_script = publish_step["run"]
    assert "gh release create" in publish_script
    assert "--prerelease" in publish_script
    assert "--draft" in publish_script
    assert "cleanup_failed_release" in publish_script
    assert 'notes_file=".github/release-notes/${RELEASE_TAG}.md"' in publish_script
    assert '--notes-file "$notes_file"' in publish_script
    assert "--generate-notes" in publish_script  # fallback only



def test_v3_beta_release_notes_cover_product_level_changes():
    notes = (ROOT / ".github/release-notes/v3.0.0-beta.1.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "统一的图形设置中心",
        "Fcitx 5 成为真正的全局 Module",
        "IBus 与 Fcitx 5 统一语音编辑",
        "原生流式 ASR 预览",
        "用户术语与原生热词",
        "可配置的中文 ITN",
        "Doctor、Playground 与反馈",
        "完整的发行版安装包",
        "v2.2.3...v3.0.0-beta.1",
    ):
        assert required in notes
    assert "## What's Changed" not in notes



def test_v3_beta2_release_notes_cover_native_package_hotfixes():
    notes = (ROOT / ".github/release-notes/v3.0.0-beta.2.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "Arch settings center startup",
        "ModuleNotFoundError: No module named 'gi'",
        "Correct native-package installation state",
        "尚未为当前用户配置；软件包已提供系统组件",
        "Explicit status refresh behavior",
        "v3.0.0-beta.1...v3.0.0-beta.2",
    ):
        assert required in notes


def test_release_documentation_explains_all_distribution_layers():
    text = (ROOT / "packaging/README.md").read_text(encoding="utf-8")
    for required in ("wheel", "sdist", "source bundle", "DEB", "RPM", "Arch", "Polkit"):
        assert required in text
    assert "do not run `pip`" in text



def test_python_packaging_uses_pep639_license_metadata():
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires = ["setuptools>=77", "wheel"]' in source
    assert 'license = "GPL-3.0-or-later"' in source
    assert 'license-files = ["LICENSE"]' in source
    assert "license = {" not in source



def test_installation_paths_cover_user_system_and_multiarch_layouts(tmp_path: Path):
    home = tmp_path / "home"
    prefix = tmp_path / "usr"
    multiarch = prefix / "lib/x86_64-linux-gnu/fcitx5/vocotype.so"
    multiarch.parent.mkdir(parents=True)
    multiarch.touch()
    paths = installation_paths(home=home, system_prefix=prefix)
    assert home / ".local/lib/fcitx5/vocotype.so" in paths.fcitx_modules
    assert home / ".local/lib64/fcitx5/vocotype.so" in paths.fcitx_modules
    assert prefix / "lib/fcitx5/vocotype.so" in paths.fcitx_modules
    assert prefix / "lib64/fcitx5/vocotype.so" in paths.fcitx_modules
    assert multiarch in paths.fcitx_modules
    assert prefix / "share/fcitx5/addon/vocotype.conf" in paths.fcitx_addons
    assert prefix / "lib/systemd/user/vocotype-fcitx5-backend.service" in paths.fcitx_services
    assert prefix / "bin/vocotype-fcitx5-backend" in paths.fcitx_backend_launchers
    assert prefix / "share/vocotype/fcitx5/backend/fcitx5_server.py" in paths.fcitx_runtime_entries
    assert prefix / "libexec/vocotype-ibus-engine" in paths.ibus_launchers
    assert prefix / "lib/vocotype/vocotype-ibus-engine" in paths.ibus_launchers
    assert prefix / "share/ibus/component/vocotype.xml" in paths.ibus_components
    assert prefix / "share/vocotype/ibus/main.py" in paths.ibus_runtime_entries
    assert home / ".local/share/vocotype-fcitx5/.venv/bin/python" in paths.python_runtimes
    for group in (
        paths.fcitx_modules,
        paths.fcitx_addons,
        paths.fcitx_services,
        paths.fcitx_backend_launchers,
        paths.fcitx_runtime_entries,
        paths.ibus_launchers,
        paths.ibus_components,
        paths.ibus_runtime_entries,
        paths.python_runtimes,
    ):
        assert len(group) == len(set(group))



def test_appstream_metadata_matches_desktop_and_package_identity():
    metadata = ROOT / "data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml"
    source = metadata.read_text(encoding="utf-8")
    assert "<id>io.github.LeonardNJU.VoCoType</id>" in source
    assert "io.github.LeonardNJU.VoCoType.Settings.desktop" in source
    assert "<project_license>GPL-3.0-or-later</project_license>" in source
    assert "https://vocotype-linux.lsamc.website" in source
    result = subprocess.run(
        ["appstreamcli", "validate", "--no-net", str(metadata)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    ) if shutil.which("appstreamcli") else None
    if result is not None:
        assert result.returncode == 0, result.stdout + result.stderr


def test_release_builder_refuses_dirty_tree_by_default():
    source = (ROOT / "packaging/tools/build-release.py").read_text(encoding="utf-8")
    assert 'git_output("status", "--porcelain", "--untracked-files=normal")' in source
    assert "--allow-dirty" in source
    assert "refusing to build release assets from a dirty working tree" in source


def test_manual_release_dispatch_does_not_treat_branch_name_as_version():
    source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "inputs.version || github.ref_name" not in source
    assert '"$GITHUB_REF_TYPE" == tag' in source
    assert "INPUT_VERSION" in source
    assert 'packaging/tools/versioning.py "$INPUT_VERSION"' in source
    assert 'GITHUB_REF_NAME" != "$tag"' in source



def test_native_packages_include_minimal_settings_runtime_dependencies(tmp_path: Path):
    for flavor in ("universal", "ibus", "fcitx5"):
        control = _render_package_metadata("debian", flavor, tmp_path)
        spec = _render_package_metadata("rpm", flavor, tmp_path)
        pkgbuild = _render_package_metadata("arch", flavor, tmp_path)
        assert "python3 (>= 3.10)" in control
        assert "python3 (>= 3.11)" not in control
        assert "python3-gi" in control and "python3-yaml" in control
        assert "python3-numpy" in control
        assert "pkexec | policykit-1" in control
        assert "python3-gobject" in spec and "python3-pyyaml" in spec
        assert "python3-numpy" in spec
        assert 'requires-python = ">=3.11,<3.13"' in (
            ROOT / "pyproject.toml"
        ).read_text(encoding="utf-8")
        assert "uv venv --python 3.12" in (
            ROOT / "packaging/tests/smoke-binary-runtime.sh"
        ).read_text(encoding="utf-8")
        assert "python-gobject" in pkgbuild and "python-yaml" in pkgbuild
        assert "python-numpy" in pkgbuild
        for source in (control, spec, pkgbuild):
            assert "funasr" not in source.casefold()
            assert "modelscope" not in source.casefold()

        includes_ibus = flavor in {"universal", "ibus"}
        includes_fcitx = flavor in {"universal", "fcitx5"}
        assert ("ibus" in control.split("\nDepends:", 1)[1].splitlines()[0]) == includes_ibus
        assert ("fcitx5" in control.split("Depends:", 1)[1].splitlines()[0]) == includes_fcitx
        assert ("Requires:       ibus" in spec) == includes_ibus
        assert ("Requires:       fcitx5" in spec) == includes_fcitx
        assert "ibus-rime" not in control
        assert "ibus-rime" not in spec
        assert "ibus-rime" not in pkgbuild
        for dependency in ("librime1", "librime-bin", "librime-data", "rime-data-luna-pinyin"):
            assert (dependency in control) == includes_ibus
        for dependency in ("librime", "librime-tools", "brise"):
            assert (dependency in spec) == includes_ibus
        for dependency in ("librime", "librime-data"):
            assert (dependency in pkgbuild) == includes_ibus

def test_release_builder_removes_non_artifact_files_from_python_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    spec = importlib.util.spec_from_file_location(
        "vocotype_build_release_cleanup", ROOT / "packaging/tools/build-release.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/uv")

    def fake_run(*_argv: str, cwd: Path = ROOT) -> None:
        output = tmp_path / "python"
        (output / ".gitignore").write_text("*", encoding="utf-8")
        (output / "vocotype_linux-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
        (output / "vocotype_linux-1.0.0.tar.gz").write_bytes(b"sdist")

    monkeypatch.setattr(module, "run", fake_run)
    artifacts = module.build_python_distributions(tmp_path)
    assert [path.name for path in artifacts] == [
        "vocotype_linux-1.0.0-py3-none-any.whl",
        "vocotype_linux-1.0.0.tar.gz",
    ]
    assert not (tmp_path / "python/.gitignore").exists()



def _write_tar(path: Path, names: list[str]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            payload = b"x"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_release_validator_accepts_complete_artifacts_and_rejects_corruption(tmp_path: Path):
    version = "1.2.3"
    commit = "a" * 40
    python_dir = tmp_path / "python"
    python_dir.mkdir()
    source = tmp_path / f"VocoType-linux-{version}.tar.gz"
    source_names = [
        f"VocoType-linux-{version}/{suffix}"
        for suffix in (
            "README.md",
            "MANIFEST.in",
            "vocotype_package.py",
            ".github/workflows/release.yml",
            "packaging/tools/stage-system-package.sh",
            "fcitx5/module/vocotype_module.cpp",
            "ibus/scripts/install-gui.sh",
            "settings_center/playground_service.py",
            "settings_center/playground_audio_worker.py",
            "data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml",
        )
    ]
    _write_tar(source, source_names)
    wheel = python_dir / f"vocotype_linux-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in (
            "app/config.py",
            "ibus/main.py",
            "settings_center/application.py",
            "settings_center/playground_service.py",
            "settings_center/playground_audio_worker.py",
            "vocotype_package.py",
            "vocotype_version.py",
            "vocotype_linux.data/share/vocotype/terms.yaml",
            "vocotype_linux.data/share/vocotype/ibus/vocotype.xml.in",
            "vocotype_linux.data/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml",
        ):
            archive.writestr(name, "x")
    sdist = python_dir / f"vocotype_linux-{version}.tar.gz"
    _write_tar(
        sdist,
        [
            f"vocotype_linux-{version}/{suffix}"
            for suffix in (
                "README.md",
                "MANIFEST.in",
                "vocotype_package.py",
                "packaging/tools/stage-system-package.sh",
                "fcitx5/module/vocotype_module.cpp",
                "ibus/scripts/install-gui.sh",
                "settings_center/playground_service.py",
                "settings_center/playground_audio_worker.py",
                "tests/test_release_packaging.py",
            )
        ],
    )

    def digest(path: Path) -> str:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()

    artifacts = [source, wheel, sdist]
    rows = [
        {
            "path": path.relative_to(tmp_path).as_posix(),
            "size": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in artifacts
    ]
    (tmp_path / "release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "vocotype-linux",
                "version": version,
                "commit": commit,
                "artifacts": rows,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "SHA256SUMS").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in rows),
        encoding="utf-8",
    )
    result = _run(
        sys.executable,
        "packaging/tools/validate-release.py",
        "--release-dir",
        str(tmp_path),
        "--expected-version",
        version,
        "--expected-commit",
        commit,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel.write_bytes(wheel.read_bytes() + b"corrupt")
    result = _run(sys.executable, "packaging/tools/validate-release.py", "--release-dir", str(tmp_path))
    assert result.returncode != 0
    assert "mismatch" in result.stderr




def test_legacy_fcitx_input_method_engine_is_not_kept_as_dead_source():
    assert not (ROOT / "fcitx5/addon").exists()
    assert (ROOT / "fcitx5/common/ipc_client.cpp").is_file()
    cmake = (ROOT / "fcitx5/module/CMakeLists.txt").read_text(encoding="utf-8")
    assert "../common/ipc_client.cpp" in cmake
    assert "../addon" not in cmake

def test_fcitx_module_uses_apis_available_since_fcitx_5014():
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    assert "#include <fcitx-utils/event.h>" in header
    assert "#include <fcitx-utils/eventdispatcher.h>" in header
    assert "eventloopinterface.h" not in header
    assert "__has_include(<fcitx-utils/standardpaths.h>)" in source
    assert "fcitx::StandardPathsType::PkgConfig" in source
    assert "fcitx::StandardPath::Type::PkgConfig" in source
    assert "CONFIG_PATH_TYPE" in source
    assert "event_dispatcher_.attach(&instance_->eventLoop())" in source
    assert "instance_->eventDispatcher()" not in source
    assert "frontendName()" not in source
    assert "CommitStringWithCursor" not in source
    assert "commitStringWithCursor" not in source



def test_systemd_backend_disables_python_bytecode_writes():
    source = (
        ROOT / "packaging/systemd/vocotype-fcitx5-backend.service"
    ).read_text(encoding="utf-8")
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in source


def test_release_packages_are_offline_but_require_complete_prebuilt_runtimes():
    debian = (ROOT / "packaging/debian/rules").read_text(encoding="utf-8")
    control = (ROOT / "packaging/debian/control").read_text(encoding="utf-8")
    rpm = (ROOT / "packaging/rpm/vocotype.spec.in").read_text(encoding="utf-8")
    arch = (ROOT / "packaging/arch/PKGBUILD.in").read_text(encoding="utf-8")
    for source in (debian, rpm, arch):
        assert "native/streaming_worker/build.sh" not in source
        assert "--require-streaming-bundle" in source
        assert "--require-wheelhouse" in source
        assert "--skip-streaming-bundle" not in source
        assert "--skip-wheelhouse" not in source
    assert "clang" not in control
    assert "curl" not in control.split("Package:", 1)[0]
    assert "BuildRequires:  clang" not in rpm
    assert "BuildRequires:  curl" not in rpm
    assert "Architecture: amd64" in control
    assert "ExclusiveArch:  x86_64" in rpm
    assert "%{_libexecdir}/vocotype-streaming-worker" in rpm
    assert "%{_libdir}/vocotype/" in rpm
    assert "arch=('x86_64')" in arch
    assert "options=('!debug' '!strip')" in arch
    assert "%global __strip /bin/true" in rpm
    assert "'clang'" not in arch
    assert "'curl'" not in arch.split("optdepends", 1)[0]

    for builder in ("build-deb.sh", "build-rpm.sh", "build-arch.sh"):
        source = (ROOT / "packaging/tools" / builder).read_text(encoding="utf-8")
        assert "VOCOTYPE_STREAMING_BUNDLE_DIR is required" in source
        assert "VOCOTYPE_WHEELHOUSE_DIR is required" in source
        assert "prepare-complete-source.py" in source
    deb_builder = (ROOT / "packaging/tools/build-deb.sh").read_text(
        encoding="utf-8"
    )
    assert 'renamed=${basename/#vocotype-linux_/${PACKAGE_NAME}_}' in deb_builder

    smoke = (ROOT / "packaging/tests/smoke-installed-package.sh").read_text(
        encoding="utf-8"
    )
    assert "PACKAGE_STREAMING_RUNTIME_OPTIONAL_ABSENT" not in smoke
    assert "PACKAGE_STREAMING_RUNTIME_OK" in smoke
    assert 'ldd -r "$streaming_worker_elf"' in smoke
    assert '"$streaming_launcher" --help' in smoke
    assert "streaming_worker_elf" in smoke
    assert "PACKAGE_WHEELHOUSE_OK" in smoke
    assert "VOCOTYPE_SETTINGS_PROBE_ONLY=1" in smoke
    assert "SETTINGS_RUNTIME_OK" in smoke
    assert '"$wheel_count" -ge 12' in smoke
    for required in ("onnxruntime", "sentencepiece", "funasr_onnx"):
        assert required in smoke
    for dependency in ("pyrime", "torch", "transformers", "socksio"):
        assert dependency in smoke
    assert "IBus runtime wheel missing" in smoke
    assert "IBus-only wheel leaked into Fcitx package" in smoke

    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert release.count("name: native-streaming-linux-x86_64") >= 4
    assert "build-native-streaming-release.sh" in release
    assert "build-runtime-wheelhouse.sh" in release
    assert "smoke-binary-runtime.sh" in release
    assert "audit-built-package.sh" in release
    assert "PyGObject==3.50.2" in release
    assert release.count("PyGObject==3.56.3") >= 2
    assert "pyrime==0.2.2" not in release
    assert "VOCOTYPE_PYRIME_SPEC" not in release
    assert release.count("wheelhouse-fcitx5") >= 6
    assert release.count("wheelhouse-ibus") >= 6
    assert "librime-bin librime-data rime-data-luna-pinyin" in release
    assert "librime librime-tools brise" in release
    assert "python-gobject gtk3 ibus librime librime-data" in release
    assert "ibus-rime" not in release
    assert release.count("for flavor in universal ibus fcitx5") >= 3
    assert '${package_name}_${debian_version}-*_amd64.deb' in release
    assert '${package_name}-${rpm_version}-${rpm_release}*.x86_64.rpm' in release
    assert '${package_name}-${arch_version}-*.pkg.tar.*' in release
    assert '${package_name}-*.x86_64.rpm' not in release
    assert '${package_name}-*.pkg.tar.*' not in release
    assert 'smoke-installed-package.sh "${{ needs.validate-version.outputs.version }}" "$flavor"' in release
    stage = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    assert '--flavor) FLAVOR=' in stage
    assert '--package-manager) PACKAGE_MANAGER=' in stage
    assert 'flavor=%s' in stage and 'package=%s' in stage
    assert "printf 'manager=%s\\n'" in stage
    assert 'rm -rf "$source_root/ibus"' in stage
    assert 'rm -rf "$source_root/fcitx5"' in stage
    assert ".wheelhouse.sha256" in stage
    assert ".native-payload.sha256" in stage
    debian_rules = (ROOT / "packaging/debian/rules").read_text(encoding="utf-8")
    assert "dh_strip_nondeterminism -X/usr/share/vocotype/wheelhouse" in debian_rules
    assert "dh_strip -X/vocotype/" in debian_rules
    assert 'streaming_launcher="$DESTDIR$LIBEXECDIR/vocotype-streaming-worker"' in stage
    assert 'ln -sfn "$streaming_link_target"' not in stage
    assert "--only-binary :all:" in (
        ROOT / "packaging/tests/smoke-binary-runtime.sh"
    ).read_text(encoding="utf-8")


def test_staging_skip_streaming_bundle_ignores_a_valid_cached_bundle(tmp_path: Path):
    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "lib").mkdir()
    worker = bundle / "bin/vocotype-streaming-worker"
    worker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    worker.chmod(0o755)
    (bundle / "lib/libfunasr.so").write_bytes(b"cached")
    dest = tmp_path / "root"
    env = os.environ.copy()
    env["VOCOTYPE_STREAMING_BUNDLE_DIR"] = str(bundle)

    result = subprocess.run(
        [
            "bash",
            "packaging/tools/stage-system-package.sh",
            "--destdir",
            str(dest),
            "--skip-module-build",
            "--skip-streaming-bundle",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "intentionally omitted" in result.stderr
    assert not list(dest.rglob("vocotype-streaming-worker"))
    assert not list(dest.rglob("libfunasr.so"))


def test_staging_streaming_require_and_skip_modes_are_mutually_exclusive(tmp_path: Path):
    result = _run(
        "bash",
        "packaging/tools/stage-system-package.sh",
        "--destdir",
        str(tmp_path / "root"),
        "--require-streaming-bundle",
        "--skip-streaming-bundle",
    )
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr


def test_native_build_pins_and_audits_official_onnxruntime():
    build = (ROOT / "native/streaming_worker/build.sh").read_text(encoding="utf-8")
    audit = (ROOT / "native/streaming_worker/audit_bundle.py").read_text(encoding="utf-8")
    assert "ONNXRUNTIME_VERSION=${VOCOTYPE_ONNXRUNTIME_VERSION:-1.23.2}" in build
    assert "1fa4dcaef22f6f7d5cd81b28c2800414350c10116f5fdd46a2160082551c5f9b" in build
    assert 'python "$SCRIPT_DIR/audit_bundle.py" "$BUNDLE_DIR"' in build
    assert "absolute RUNPATH" in audit
    assert "unbundled dependency" in audit


def test_wheelhouse_builder_uses_locked_core_runtime_only():
    source = (ROOT / "packaging/tools/build-runtime-wheelhouse.sh").read_text(
        encoding="utf-8"
    )
    assert "https://pypi.org/simple" in source
    assert '--index-url "$INDEX_URL"' in source
    assert '"$UV" export --locked --no-dev --no-emit-project' in source
    assert "--all-extras" not in source
    assert '--constraint "$work/locked-constraints.txt"' in source
    assert "'/^[Pp]y[Gg][Oo]bject==/d'" in source
    assert "torch" not in source
    assert "pyrime" not in source
    assert "wcwidth" not in source
    assert "VOCOTYPE_BASE_WHEELHOUSE_DIR" in source
    assert "--wheel-dir" in source
    assert "audit-wheelhouse.py" in source
    assert "must pin one exact version" in source
    assert "PyGObject==3.50.2" in source
    assert "VOCOTYPE_PYRIME_SPEC" not in source
    assert "--flavor" in source
    runtime = (ROOT / "installers/runtime-common.sh").read_text(encoding="utf-8")
    assert '--no-index --find-links "$wheelhouse"' in runtime
    assert "--only-binary=:all:" in runtime


def test_release_assets_are_flattened_before_checksums_and_publication(tmp_path: Path):
    source = tmp_path / "downloaded"
    (source / "one").mkdir(parents=True)
    (source / "two").mkdir()
    (source / "one/vocotype-linux_3.0.0~beta1-1_amd64.deb").write_bytes(
        b"debian-beta"
    )
    (source / "two/vocotype-linux-3.0.0-0.beta1.fc44.x86_64.rpm").write_bytes(
        b"rpm"
    )
    (source / "two/vocotype-linux-3.0.0-0.beta1.fc44.src.rpm").write_bytes(
        b"source-rpm"
    )
    (source / "two/vocotype_linux-3.0.0b1-py3-none-any.whl").write_bytes(
        b"wheel"
    )
    (source / "two/release-assets.json").write_text("{}", encoding="utf-8")

    destination = tmp_path / "final"
    result = _run(
        sys.executable,
        "packaging/tools/collect-release-assets.py",
        str(source),
        str(destination),
        "--installers-only",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in destination.iterdir()) == [
        "vocotype-linux-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux_3.0.0.beta1-1_amd64.deb",
    ]
    assert (
        destination / "vocotype-linux_3.0.0.beta1-1_amd64.deb"
    ).read_bytes() == b"debian-beta"
    assert not any("~" in path.name for path in destination.iterdir())

    duplicate = source / "two/vocotype-linux_3.0.0~beta1-1_amd64.deb"
    duplicate.write_bytes(b"duplicate")
    rejected = _run(
        sys.executable,
        "packaging/tools/collect-release-assets.py",
        str(source),
        str(destination),
        "--installers-only",
    )
    assert rejected.returncode != 0
    assert "duplicate normalized release asset name" in rejected.stderr

    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "collect-release-assets.py" in workflow
    assert "assets final-assets --installers-only" in workflow
    assert "validate-final-release-assets.py final-assets" in workflow
    assert "name: final-release-assets" in workflow
    assert "cd final-assets" in workflow
    assert "SHA256SUMS" in workflow
    assert "SHA256SUMS.all" not in workflow
    assert "release-assets.json" not in workflow
    assert 'gh release create "$RELEASE_TAG" final-assets/*' in workflow


def _write_installer_checksum_file(root: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(root.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_final_release_asset_validator_accepts_only_installers(tmp_path: Path):
    final = tmp_path / "final"
    final.mkdir()
    names = (
        "vocotype-linux_3.0.0.beta1-1_amd64.deb",
        "vocotype-linux-ibus_3.0.0.beta1-1_amd64.deb",
        "vocotype-linux-fcitx5_3.0.0.beta1-1_amd64.deb",
        "vocotype-linux-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux-ibus-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux-fcitx5-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux-3.0.0b1-1-x86_64.pkg.tar.zst",
        "vocotype-linux-ibus-3.0.0b1-1-x86_64.pkg.tar.zst",
        "vocotype-linux-fcitx5-3.0.0b1-1-x86_64.pkg.tar.zst",
    )
    for index, name in enumerate(names):
        (final / name).write_bytes(f"asset-{index}".encode())
    _write_installer_checksum_file(final)

    valid = _run(
        sys.executable,
        "packaging/tools/validate-final-release-assets.py",
        str(final),
        "--version",
        "3.0.0b1",
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert "FINAL_RELEASE_INSTALLERS_OK" in valid.stdout

    forbidden = final / "release-assets.json"
    forbidden.write_text("{}", encoding="utf-8")
    rejected = _run(
        sys.executable,
        "packaging/tools/validate-final-release-assets.py",
        str(final),
        "--version",
        "3.0.0b1",
    )
    assert rejected.returncode != 0
    assert "exactly 9 installers and SHA256SUMS" in rejected.stderr


def test_final_release_asset_validator_rejects_bad_checksum(tmp_path: Path):
    final = tmp_path / "final"
    final.mkdir()
    names = (
        "vocotype-linux_3.0.0.beta1-1_amd64.deb",
        "vocotype-linux-ibus_3.0.0.beta1-1_amd64.deb",
        "vocotype-linux-fcitx5_3.0.0.beta1-1_amd64.deb",
        "vocotype-linux-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux-ibus-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux-fcitx5-3.0.0-0.beta1.fc44.x86_64.rpm",
        "vocotype-linux-3.0.0b1-1-x86_64.pkg.tar.zst",
        "vocotype-linux-ibus-3.0.0b1-1-x86_64.pkg.tar.zst",
        "vocotype-linux-fcitx5-3.0.0b1-1-x86_64.pkg.tar.zst",
    )
    for name in names:
        (final / name).write_bytes(b"installer")
    _write_installer_checksum_file(final)
    (final / names[0]).write_bytes(b"tampered")
    rejected = _run(
        sys.executable,
        "packaging/tools/validate-final-release-assets.py",
        str(final),
        "--version",
        "3.0.0b1",
    )
    assert rejected.returncode != 0
    assert "checksum index mismatch" in rejected.stderr


def test_release_candidate_versions_sort_before_formal_versions():
    assert _version_field("python", "v3.0.0-beta.1") == "3.0.0b1"
    assert _version_field("tag", "3.0.0b1") == "v3.0.0-beta.1"
    assert _version_field("debian", "3.0.0b1") == "3.0.0~beta1"
    assert _version_field("rpm_release", "3.0.0b1") == "0.beta1"
    assert _version_field("arch", "3.0.0b1") == "3.0.0b1"
    assert _version_field("prerelease", "3.0.0b1") == "true"
    assert _version_field("python", "v3.0.0-rc.1") == "3.0.0rc1"
    assert _version_field("tag", "3.0.0rc1") == "v3.0.0-rc.1"
    assert _version_field("debian", "3.0.0rc1") == "3.0.0~rc1"
    assert _version_field("rpm_version", "3.0.0rc1") == "3.0.0"
    assert _version_field("rpm_release", "3.0.0rc1") == "0.rc1"
    assert _version_field("arch", "3.0.0rc1") == "3.0.0rc1"
    assert _version_field("prerelease", "3.0.0rc1") == "true"
    assert _version_field("tag", "3.0.0") == "v3.0.0"
    assert _version_field("rpm_release", "3.0.0") == "1"
    assert _version_field("prerelease", "3.0.0") == "false"

    if shutil.which("vercmp"):
        beta_to_rc = subprocess.run(
            ["vercmp", "3.0.0b1", "3.0.0rc1"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert int(beta_to_rc.stdout.strip()) < 0
        result = subprocess.run(
            ["vercmp", "3.0.0rc1", "3.0.0"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert int(result.stdout.strip()) < 0
    if shutil.which("dpkg"):
        beta_to_rc = subprocess.run(
            ["dpkg", "--compare-versions", "3.0.0~beta1", "lt", "3.0.0~rc1"],
            check=False,
        )
        assert beta_to_rc.returncode == 0
        result = subprocess.run(
            ["dpkg", "--compare-versions", "3.0.0~rc1", "lt", "3.0.0"],
            check=False,
        )
        assert result.returncode == 0


def _write_common_runtime_wheels(root: Path) -> None:
    required = {
        "funasr_onnx": "0.4.2-py3-none-any",
        "jieba": "0.42.1-py3-none-any",
        "modelscope": "1.30.0-py3-none-any",
        "numpy": "1.26.4-cp312-cp312-linux_x86_64",
        "onnxruntime": "1.23.2-cp312-cp312-linux_x86_64",
        "PyYAML": "6.0.3-cp312-cp312-linux_x86_64",
        "scipy": "1.16.3-cp312-cp312-linux_x86_64",
        "sentencepiece": "0.2.1-cp312-cp312-linux_x86_64",
        "sounddevice": "0.5.2-py3-none-any",
        "soundfile": "0.13.1-py3-none-any",
    }
    for name, suffix in required.items():
        version = suffix.split("-", 1)[0]
        _write_minimal_wheel(root / f"{name}-{suffix}.whl", name, version)


def test_wheelhouse_audit_enforces_framework_profiles(tmp_path: Path):
    fcitx = tmp_path / "fcitx5"
    ibus = tmp_path / "ibus"
    fcitx.mkdir()
    ibus.mkdir()
    _write_common_runtime_wheels(fcitx)
    _write_common_runtime_wheels(ibus)

    fcitx_result = _run(
        sys.executable,
        "packaging/tools/audit-wheelhouse.py",
        str(fcitx),
        "--flavor",
        "fcitx5",
    )
    assert fcitx_result.returncode == 0, fcitx_result.stdout + fcitx_result.stderr

    for name, suffix in {
        "PyGObject": "3.50.2-cp312-cp312-linux_x86_64",
        "pycairo": "1.29.0-cp312-cp312-linux_x86_64",
    }.items():
        version = suffix.split("-", 1)[0]
        _write_minimal_wheel(ibus / f"{name}-{suffix}.whl", name, version)
    ibus_result = _run(
        sys.executable,
        "packaging/tools/audit-wheelhouse.py",
        str(ibus),
        "--flavor",
        "ibus",
        "--expected-pygobject-version",
        "3.50.2",
    )
    assert ibus_result.returncode == 0, ibus_result.stdout + ibus_result.stderr

    leaked = fcitx / "pyrime-0.2.2-cp312-cp312-linux_x86_64.whl"
    _write_minimal_wheel(leaked, "pyrime", "0.2.2")
    rejected = _run(
        sys.executable,
        "packaging/tools/audit-wheelhouse.py",
        str(fcitx),
        "--flavor",
        "fcitx5",
    )
    assert rejected.returncode != 0
    assert "forbidden wheels present for flavor=fcitx5" in rejected.stderr

    leaked.unlink()
    (ibus / "PyGObject-3.50.2-cp312-cp312-linux_x86_64.whl").unlink()
    missing = _run(
        sys.executable,
        "packaging/tools/audit-wheelhouse.py",
        str(ibus),
        "--flavor",
        "ibus",
    )
    assert missing.returncode != 0
    assert "required wheels missing for flavor=ibus: pygobject" in missing.stderr

    protobuf = fcitx / "protobuf-6.33.2-cp39-abi3-manylinux2014_x86_64.whl"
    _write_minimal_wheel(protobuf, "protobuf", "6.33.2")
    protobuf.write_bytes(protobuf.read_bytes()[:32])
    corrupted = _run(
        sys.executable,
        "packaging/tools/audit-wheelhouse.py",
        str(fcitx),
        "--flavor",
        "fcitx5",
    )
    assert corrupted.returncode != 0
    assert "invalid wheel archive" in corrupted.stderr

    smoke = (ROOT / "packaging/tests/smoke-binary-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert "PACKAGE_RIME_KEYBOARD_OK" in smoke
    assert "rime_runtime.py" in smoke
    assert "--schema luna_pinyin --key n" in smoke
    assert "PACKAGE_FCITX_PRIVATE_RUNTIME_MINIMAL_OK" in smoke
    runtime = (ROOT / "installers/runtime-common.sh").read_text(encoding="utf-8")
    optional_body = runtime.split("install_binary_packages()", 1)[1].split(
        "install_native_streaming_bundle()", 1
    )[0]
    assert 'repository_args=(--no-index --find-links "$wheelhouse")' in optional_body
    assert "--only-binary" in optional_body
