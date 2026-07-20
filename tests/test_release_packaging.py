from __future__ import annotations

import importlib.util
import io
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
    for excluded in (
        ".github",
        "packaging",
        "test",
        "packaging/tools/build-release.py",
        "packaging/tools/validate-release.py",
        "packaging/tools/build-deb.sh",
        "packaging/tools/build-rpm.sh",
        "packaging/tools/build-arch.sh",
    ):
        assert not (source_root / excluded).exists(), excluded


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


def test_settings_launcher_prefers_user_runtime(tmp_path: Path):
    home = tmp_path / "home"
    fake_python = home / ".local/share/vocotype/.venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    receipt = tmp_path / "receipt.json"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(receipt)!r}, 'w').write(json.dumps({{'argv': sys.argv, 'root': os.environ.get('VOCOTYPE_PROJECT_DIR'), 'pythonpath': os.environ.get('PYTHONPATH')}}))\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update({"HOME": str(home), "VOCOTYPE_SYSTEM_ROOT": "/opt/vocotype-test", "PYTHONPATH": "tail"})
    result = subprocess.run(
        [str(ROOT / "packaging/bin/vocotype-settings"), "--example"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert data["argv"][1:] == ["-m", "settings_center.application", "--example"]
    assert data["root"] == "/opt/vocotype-test"
    assert data["pythonpath"].startswith("/opt/vocotype-test")


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
    changelog = (ROOT / "packaging/debian/changelog").read_text(encoding="utf-8")
    assert changelog.startswith(f"vocotype-linux ({version}-1)")
    cmake = (ROOT / "fcitx5/module/CMakeLists.txt").read_text(encoding="utf-8")
    assert "vocotype_version.py" in cmake
    assert "VERSION 2.3.0" not in cmake
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


def test_native_package_recipes_share_one_staging_contract():
    recipes = [
        ROOT / "packaging/debian/rules",
        ROOT / "packaging/rpm/vocotype.spec.in",
        ROOT / "packaging/arch/PKGBUILD.in",
    ]
    for recipe in recipes:
        source = recipe.read_text(encoding="utf-8")
        assert "packaging/tools/stage-system-package.sh" in source
        assert "pip install" not in source
        assert "download_models" not in source
    control = (ROOT / "packaging/debian/control").read_text(encoding="utf-8")
    assert "Package: vocotype-linux" in control
    assert "Architecture: any" in control
    spec = (ROOT / "packaging/rpm/vocotype.spec.in").read_text(encoding="utf-8")
    assert "License:        GPL-3.0-or-later" in spec
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD.in").read_text(encoding="utf-8")
    assert "sha256sums=('@SOURCE_SHA256@')" in pkgbuild
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
    assert ".system-package" in fcitx
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


def test_native_package_smoke_runs_an_isolated_ibus_registry():
    smoke = (ROOT / "packaging/tests/smoke-installed-package.sh").read_text(encoding="utf-8")
    registry = (ROOT / "packaging/tests/smoke-ibus-registry.sh").read_text(encoding="utf-8")
    assert "smoke-ibus-registry.sh" in smoke
    assert "dbus-run-session" in registry
    assert "GIO_USE_VFS=local" in registry
    assert "ibus-daemon" in registry
    assert "IBUS_REGISTRY_SMOKE_OK" in registry

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
    assert "softprops/action-gh-release@v3" in release_text
    assert release["jobs"]["publish"]["needs"] == ["source-python-deb", "rpm", "arch"]


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
    assert 'args+=(--expected-version "$expected")' in source



def test_native_packages_include_minimal_settings_runtime_dependencies():
    control = (ROOT / "packaging/debian/control").read_text(encoding="utf-8")
    spec = (ROOT / "packaging/rpm/vocotype.spec.in").read_text(encoding="utf-8")
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD.in").read_text(encoding="utf-8")
    assert "python3-gi" in control and "python3-yaml" in control
    assert "pkexec | policykit-1" in control
    assert "python3-gobject" in spec and "python3-pyyaml" in spec
    assert "python-gobject" in pkgbuild and "python-yaml" in pkgbuild
    for source in (control, spec, pkgbuild):
        assert "funasr" not in source.casefold()
        assert "modelscope" not in source.casefold()



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
            ".github/workflows/release.yml",
            "packaging/tools/stage-system-package.sh",
            "fcitx5/module/vocotype_module.cpp",
            "ibus/scripts/install-gui.sh",
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
                "packaging/tools/stage-system-package.sh",
                "fcitx5/module/vocotype_module.cpp",
                "ibus/scripts/install-gui.sh",
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
    assert "fcitx::StandardPath::Type::PkgConfig" in source
    assert "fcitx::StandardPathsType::PkgConfig" not in source
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
