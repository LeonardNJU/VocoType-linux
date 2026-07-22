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




def _run(*argv: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        **kwargs,
    )





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

def test_native_stage_contract_is_safe_compiled_and_unique():
    source = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    required = (
        "native/desktop",
        "vocotype-audio-recorder",
        "vocotype-model-manager",
        "vocotype-settings",
        "vocotype-core",
        "vocotype-streaming-worker",
        "vocotype-offline-worker",
        "runtime=native",
    )
    for value in required:
        assert value in source
    for forbidden in (
        "runtime-files.txt",
        "audit-wheelhouse.py",
        "build-runtime-wheelhouse.sh",
        "vendor/wheelhouse",
        "settings_center/application.py",
        "ibus/main.py",
    ):
        assert forbidden not in source
    assert source.count("install-native-user.sh") == 2



def test_staging_script_rejects_root_destination():
    result = _run("bash", "packaging/tools/stage-system-package.sh", "--destdir", "/", "--skip-module-build")
    assert result.returncode == 2
    assert "Refusing" in result.stderr


def test_staging_script_builds_complete_noninteractive_tree():
    source = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    cmake = (ROOT / "native/desktop/CMakeLists.txt").read_text(encoding="utf-8")
    assert "runtime=native" in source
    assert "native/desktop" in source
    assert "add_executable(vocotype-audio-recorder" in cmake
    assert "add_executable(vocotype-model-manager" in cmake
    assert "add_executable(vocotype-settings" in cmake
    assert "vendor/wheelhouse" not in source
    assert "runtime-requirements" not in source





def test_staging_script_places_prebuilt_native_streaming_bundle():
    source = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    for executable in ("vocotype-core", "vocotype-streaming-worker", "vocotype-offline-worker"):
        assert executable in source
    assert ".native-payload.sha256" in source
    assert "--require-streaming-bundle" in source


def test_staging_script_honors_custom_libexec_directory():
    source = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    assert '--libexecdir' in source
    assert 'LIBEXECDIR=${LIBEXECDIR:-"$PREFIX/libexec"}' in source
    assert 'DESTDIR$LIBEXECDIR' in source



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


def test_staging_script_honors_multilib_directory():
    source = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    assert '--libdir' in source
    assert 'runtime_libdir="$PREFIX/$LIBDIR/vocotype"' in source
    assert 'CMAKE_INSTALL_LIBDIR' in source



def test_system_launchers_never_install_dependencies_or_request_privilege():
    for relative in (
        "packaging/bin/vocotype-fcitx5-backend",
        "packaging/bin/vocotype-fcitx5-recorder",
        "packaging/bin/vocotype-settings",
        "packaging/bin/vocotype-ibus-engine",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8").lower()
        for forbidden in ("pip install", "apt-get", "dnf install", "pacman -s", "pkexec", "sudo"):
            assert forbidden not in source



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


def test_settings_launcher_prefers_distro_python_with_complete_gtk_runtime():
    source = (ROOT / "packaging/bin/vocotype-settings").read_text(encoding="utf-8")
    assert "vocotype-native/bin/vocotype-settings" in source
    assert "python" not in source.lower()



def test_settings_launcher_falls_back_to_compatible_user_runtime():
    source = (ROOT / "installers/launch-settings.sh").read_text(encoding="utf-8")
    assert "build/native-desktop" in source
    assert ".local/lib/vocotype-native/bin/vocotype-settings" in source
    assert "python" not in source.lower()



def test_settings_launcher_probe_reports_selected_runtime():
    cmake = (ROOT / "native/desktop/CMakeLists.txt").read_text(encoding="utf-8")
    source = (ROOT / "native/desktop/src/settings_main.cpp").read_text(encoding="utf-8")
    assert "add_executable(vocotype-settings" in cmake
    assert "GtkApplication" in source
    assert "PyGObject" not in source
    assert "PySide" not in source
    assert "PyQt" not in source




def test_backend_launcher_fails_cleanly_before_gui_setup():
    source = (ROOT / "packaging/bin/vocotype-fcitx5-backend").read_text(encoding="utf-8")
    assert "native core is not installed" in source.lower()
    assert "exit 78" in source
    assert "python" not in source.lower()



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


def test_native_package_recipes_share_one_staging_contract():
    for relative in ("packaging/debian/rules", "packaging/rpm/vocotype.spec.in", "packaging/arch/PKGBUILD.in"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "stage-system-package.sh" in source
        assert "--require-streaming-bundle" in source
        assert "require-wheelhouse" not in source


def test_debian_maintainer_scripts_are_noninteractive_and_offline():
    for name in ("postinst", "postrm"):
        source = (ROOT / "packaging/debian" / name).read_text(encoding="utf-8")
        for forbidden in ("read ", "/dev/tty", "curl ", "wget ", "pip ", "systemctl --user"):
            assert forbidden not in source


def test_system_package_reuse_paths_are_covered_by_installers():
    source = (ROOT / "installers/install-native-user.sh").read_text(encoding="utf-8")
    assert 'PACKAGE_MARKER="$SYSTEM_ROOT/.system-package"' in source
    assert 'native/desktop/CMakeLists.txt' in source
    assert 'SOURCE_MODE=true' in source
    assert 'elif [[ ! -f "$PACKAGE_MARKER" ]]' in source
    assert "resolve_system_binary" in source




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
    assert "/usr/share/vocotype/installers/install-native-user.sh" in smoke
    assert "/usr/share/vocotype/installers/uninstall-native-user.sh" in smoke
    assert "runtime=native" in smoke
    assert "PACKAGE_NATIVE_RUNTIME_OK" in smoke
    stage = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    assert "install-native-user.sh" in stage
    assert "uninstall-native-user.sh" in stage
    assert "settings_center" not in stage


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
        "source-deb",
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
    assert "source-python-deb" not in release_text
    assert "twine" not in release_text
    assert "dist/release/python" not in release_text




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


def test_release_documentation_explains_native_distribution_layers():
    text = (ROOT / "packaging/README.md").read_text(encoding="utf-8")
    for required in ("DEB", "RPM", "Arch", "ELF", "native", "model manager"):
        assert required in text
    assert "do **not** contain a Python interpreter" in text
    assert "wheelhouse" in text
    assert "not an installed runtime dependency" in text




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
    for format_name in ("debian", "rpm", "arch"):
        rendered = _render_package_metadata(format_name, "universal", tmp_path).lower()
        for forbidden in ("python3-gi", "python-gobject", "python3-numpy", "wheelhouse", "virtualenv"):
            assert forbidden not in rendered
        assert "gtk" in rendered
        assert "portaudio" in rendered
        assert "yaml" in rendered


def test_release_builder_produces_only_source_archive_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location(
        "vocotype_build_release_source_only", ROOT / "packaging/tools/build-release.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not hasattr(module, "build_python_distributions")
    source = tmp_path / "VocoType-linux-1.0.0.tar.gz"
    source.write_bytes(b"source")
    module.write_metadata(tmp_path, "1.0.0", "a" * 40, [source])
    manifest = json.loads((tmp_path / "release-manifest.json").read_text(encoding="utf-8"))
    assert [row["path"] for row in manifest["artifacts"]] == [source.name]
    assert not (tmp_path / "python").exists()




def _write_tar(path: Path, names: list[str]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            payload = b"x"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_release_validator_accepts_source_archive_and_rejects_corruption(tmp_path: Path):
    version = "1.2.3"
    commit = "a" * 40
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
            "native/desktop/CMakeLists.txt",
            "native/desktop/src/settings_main.cpp",
            "native/desktop/src/ibus_main.cpp",
            "native/desktop/src/model_manager_main.cpp",
            "installers/install-native-user.sh",
            "data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml",
        )
    ]
    _write_tar(source, source_names)

    def digest(path: Path) -> str:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()

    row = {
        "path": source.name,
        "size": source.stat().st_size,
        "sha256": digest(source),
    }
    (tmp_path / "release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "vocotype-linux",
                "version": version,
                "commit": commit,
                "artifacts": [row],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "SHA256SUMS").write_text(
        f"{row['sha256']}  {row['path']}\n", encoding="utf-8"
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
    source.write_bytes(source.read_bytes() + b"corrupt")
    result = _run(
        sys.executable,
        "packaging/tools/validate-release.py",
        "--release-dir",
        str(tmp_path),
    )
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



def test_fcitx_backend_launcher_prefers_native_and_keeps_python_fallback():
    source = (ROOT / "packaging/bin/vocotype-fcitx5-backend").read_text(encoding="utf-8")
    assert "vocotype-core" in source
    assert "--enable-final-asr" in source
    assert "python" not in source.lower()
    assert "legacy" not in source.lower()



def test_systemd_backend_disables_python_bytecode_writes():
    source = (ROOT / "packaging/systemd/vocotype-fcitx5-backend.service").read_text(encoding="utf-8")
    assert "VoCoType Native" in source
    assert "PYTHON" not in source
    assert "Restart=on-failure" in source



def test_release_packages_are_offline_but_require_complete_prebuilt_runtimes():
    for relative in ("packaging/tools/build-deb.sh", "packaging/tools/build-rpm.sh", "packaging/tools/build-arch.sh"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "VOCOTYPE_STREAMING_BUNDLE_DIR" in source
        assert "VOCOTYPE_WHEELHOUSE_DIR" not in source
        assert "--wheelhouse" not in source



def test_staging_skip_streaming_bundle_ignores_a_valid_cached_bundle():
    source = (ROOT / "packaging/tools/stage-system-package.sh").read_text(encoding="utf-8")
    assert 'if [[ "$SKIP_STREAMING_BUNDLE" == 1 ]]' in source
    assert 'native ASR bundle omitted' in source



def test_staging_streaming_require_and_skip_modes_are_mutually_exclusive(tmp_path: Path):
    result = _run(
        "bash", "packaging/tools/stage-system-package.sh",
        "--destdir", str(tmp_path / "root"),
        "--require-streaming-bundle", "--skip-streaming-bundle",
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


def test_obsolete_wheelhouse_tools_are_not_referenced_by_native_release_paths():
    paths = [
        ROOT / ".github/workflows/ci.yml",
        ROOT / ".github/workflows/release.yml",
        ROOT / "packaging/tools/build-deb.sh",
        ROOT / "packaging/tools/build-rpm.sh",
        ROOT / "packaging/tools/build-arch.sh",
        ROOT / "packaging/tools/stage-system-package.sh",
        ROOT / "installers/install-native-user.sh",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "build-runtime-wheelhouse.sh",
        "VOCOTYPE_WHEELHOUSE_DIR",
        "vendor/wheelhouse",
        "runtime-common.sh",
        "PyGObject==",
    ):
        assert forbidden not in combined



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




