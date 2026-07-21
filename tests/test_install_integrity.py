from __future__ import annotations

import json
from pathlib import Path

from settings_center.install_integrity import (
    KEY_FILES,
    build_integrity_manifest,
    probe_installation_integrity,
    sha256_file,
)


def _minimal_project(root: Path) -> None:
    for relative in KEY_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "vocotype_version.py":
            path.write_text('__version__ = "2.2.3"\n', encoding="utf-8")
        else:
            path.write_text(f"# {relative}\n", encoding="utf-8")


def test_manifest_hashes_all_key_files(tmp_path: Path):
    _minimal_project(tmp_path)
    manifest = build_integrity_manifest(tmp_path)
    assert manifest["version"] == "2.2.3"
    assert set(manifest["files"]) == set(KEY_FILES)
    assert manifest["files"]["app/config.py"] == sha256_file(
        tmp_path / "app/config.py"
    )


def test_integrity_probe_detects_partial_user_runtime(tmp_path: Path):
    home = tmp_path / "home"
    runtime = home / ".local/share/vocotype-fcitx5"
    (runtime / "app").mkdir(parents=True)
    (runtime / "settings_center").mkdir(parents=True)
    (runtime / "backend").mkdir(parents=True)
    (runtime / "vocotype_version.py").write_text(
        '__version__ = "2.2.3"\n', encoding="utf-8"
    )
    (runtime / "app/config.py").write_text("current\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "version": "2.2.3",
        "files": {
            "vocotype_version.py": sha256_file(runtime / "vocotype_version.py"),
            "app/config.py": sha256_file(runtime / "app/config.py"),
            "settings_center/application.py": "0" * 64,
        },
    }
    report = probe_installation_integrity(
        manifest,
        home=home,
        system_prefix=tmp_path / "usr",
    )
    assert report.status == "fail"
    assert report.missing_files == 1
    assert "MISSING settings_center/application.py" in report.details


def test_integrity_probe_passes_matching_system_runtime(tmp_path: Path):
    home = tmp_path / "home"
    root = tmp_path / "usr/share/vocotype"
    (root / "app").mkdir(parents=True)
    (root / "vocotype_version.py").write_text(
        '__version__ = "2.2.3"\n', encoding="utf-8"
    )
    (root / "app/config.py").write_text("same\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "version": "2.2.3",
        "files": {
            "vocotype_version.py": sha256_file(root / "vocotype_version.py"),
            "app/config.py": sha256_file(root / "app/config.py"),
        },
    }
    report = probe_installation_integrity(
        manifest,
        home=home,
        system_prefix=tmp_path / "usr",
    )
    assert report.status == "pass"
    assert report.mismatched_files == 0
    assert report.missing_files == 0


def test_repository_integrity_manifest_is_current():
    import subprocess

    result = subprocess.run(
        [str(Path("tools/generate-integrity-manifest.py")), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_source_installers_copy_integrity_manifest():
    for installer in (
        Path("fcitx5/scripts/install.sh"),
        Path("ibus/scripts/install.sh"),
    ):
        source = installer.read_text(encoding="utf-8")
        assert 'data/install-integrity.json' in source
        assert '$INSTALL_DIR/install-integrity.json' in source
