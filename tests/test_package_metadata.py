from __future__ import annotations

from pathlib import Path

import pytest

from vocotype_package import package_flavor_metadata, read_system_package_marker


def test_package_flavor_metadata_normalizes_aliases_and_conflicts():
    metadata = package_flavor_metadata("fcitx")
    assert metadata["flavor"] == "fcitx5"
    assert metadata["package_name"] == "vocotype-linux-fcitx5"
    assert metadata["includes_ibus"] is False
    assert metadata["includes_fcitx5"] is True
    assert metadata["conflicts"] == ["vocotype-linux", "vocotype-linux-ibus"]


def test_package_flavor_metadata_rejects_unknown_flavor():
    with pytest.raises(ValueError, match="unknown package flavor"):
        package_flavor_metadata("wayland")


def test_package_marker_preserves_legacy_defaults(tmp_path: Path):
    marker = tmp_path / ".system-package"
    marker.write_text("version=3.0.0rc1\nflavor=invalid\n", encoding="utf-8")
    assert read_system_package_marker(marker) == {
        "version": "3.0.0rc1",
        "flavor": "universal",
        "package": "vocotype-linux",
    }


def test_package_marker_validates_package_manager(tmp_path: Path):
    marker = tmp_path / ".system-package"
    marker.write_text(
        "version=3.0.0b1\nflavor=ibus\npackage=vocotype-linux-ibus\n"
        "manager=PACMAN\n",
        encoding="utf-8",
    )
    assert read_system_package_marker(marker)["manager"] == "pacman"

    marker.write_text(
        "version=3.0.0b1\nflavor=ibus\nmanager=brew\n",
        encoding="utf-8",
    )
    assert "manager" not in read_system_package_marker(marker)
