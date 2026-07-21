#!/usr/bin/env python3
"""Render native package metadata for one integration flavor."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vocotype_package import package_flavor_metadata

_PLACEHOLDER = re.compile(r"@[A-Z][A-Z0-9_]*@")


def debian_values(meta: dict[str, object]) -> dict[str, str]:
    includes_ibus = bool(meta["includes_ibus"])
    includes_fcitx = bool(meta["includes_fcitx5"])
    build = ["debhelper-compat (= 13)", "python3"]
    if includes_fcitx:
        build += ["cmake", "g++", "pkg-config", "libfcitx5core-dev", "nlohmann-json3-dev"]
    depends = [
        "${shlibs:Depends}", "${misc:Depends}", "python3 (>= 3.10)",
        "python3-gi", "python3-yaml", "gir1.2-gtk-3.0", "libportaudio2",
        "pkexec | policykit-1",
    ]
    if includes_ibus:
        depends.append("ibus")
    if includes_fcitx:
        depends.append("fcitx5")
    return {
        "@PACKAGE_NAME@": str(meta["package_name"]),
        "@BUILD_DEPENDS@": ", ".join(build),
        "@DEPENDS@": ", ".join(depends),
        "@CONFLICTS@": ", ".join(meta["conflicts"]),
        "@SUMMARY@": str(meta["summary"]),
        "@TITLE@": str(meta["title"]),
    }


def rpm_values(meta: dict[str, object]) -> dict[str, str]:
    includes_ibus = bool(meta["includes_ibus"])
    includes_fcitx = bool(meta["includes_fcitx5"])
    build = ["BuildRequires:  python3", "BuildRequires:  systemd-rpm-macros"]
    if includes_fcitx:
        build += [
            "BuildRequires:  cmake", "BuildRequires:  gcc-c++",
            "BuildRequires:  pkgconfig", "BuildRequires:  fcitx5-devel",
            "BuildRequires:  nlohmann-json-devel",
        ]
    requires = [
        "Requires:       python3 >= 3.11", "Requires:       python3-gobject",
        "Requires:       python3-pyyaml", "Requires:       gtk3",
        "Requires:       portaudio", "Requires:       polkit",
    ]
    files = []
    if includes_ibus:
        requires.append("Requires:       ibus")
        files += [
            "%{_libexecdir}/vocotype-ibus-engine",
            "%{_datadir}/ibus/component/vocotype.xml",
        ]
    if includes_fcitx:
        requires.append("Requires:       fcitx5")
        files += [
            "%{_bindir}/vocotype-fcitx5-backend",
            "%{_bindir}/vocotype-fcitx5-recorder",
            "%{_libdir}/fcitx5/vocotype.so",
            "%{_datadir}/fcitx5/addon/vocotype.conf",
            "%{_userunitdir}/vocotype-fcitx5-backend.service",
        ]
    conflicts = "\n".join(f"Conflicts:      {name}" for name in meta["conflicts"])
    return {
        "@PACKAGE_NAME@": str(meta["package_name"]),
        "@FLAVOR@": str(meta["flavor"]),
        "@SUMMARY@": str(meta["summary"]),
        "@TITLE@": str(meta["title"]),
        "@BUILD_REQUIRES@": "\n".join(build),
        "@REQUIRES@": "\n".join(requires),
        "@CONFLICTS@": conflicts,
        "@FRAMEWORK_FILES@": "\n".join(files),
    }


def arch_values(meta: dict[str, object]) -> dict[str, str]:
    includes_ibus = bool(meta["includes_ibus"])
    includes_fcitx = bool(meta["includes_fcitx5"])
    depends = ["python>=3.11", "python-gobject", "python-yaml", "gtk3", "portaudio", "polkit"]
    makedepends: list[str] = []
    if includes_ibus:
        depends.append("ibus")
    if includes_fcitx:
        depends.append("fcitx5")
        makedepends += ["cmake", "gcc", "pkgconf", "nlohmann-json"]
    quote = lambda items: " ".join(repr(item) for item in items)
    return {
        "@PACKAGE_NAME@": str(meta["package_name"]),
        "@FLAVOR@": str(meta["flavor"]),
        "@SUMMARY@": str(meta["summary"]),
        "@DEPENDS@": quote(depends),
        "@MAKEDEPENDS@": quote(makedepends),
        "@CONFLICTS@": quote(list(meta["conflicts"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("debian", "rpm", "arch"), required=True)
    parser.add_argument("--flavor", required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    meta = package_flavor_metadata(args.flavor)
    values = {
        "debian": debian_values,
        "rpm": rpm_values,
        "arch": arch_values,
    }[args.format](meta)
    text = args.template.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(key, value)
    allowed_later = {
        "@VERSION@",
        "@RELEASE@",
        "@SOURCE_VERSION@",
        "@SOURCE_SHA256@",
    }
    unresolved = sorted(set(_PLACEHOLDER.findall(text)) - allowed_later)
    if unresolved:
        raise SystemExit("unresolved flavor placeholders: " + ", ".join(unresolved))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
