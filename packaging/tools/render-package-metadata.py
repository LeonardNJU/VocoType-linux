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
    build = [
        "debhelper-compat (= 13)", "python3", "cmake", "g++", "pkg-config",
        "libportaudio2-dev", "libgtk-3-dev", "libyaml-cpp-dev",
        "libcurl4-openssl-dev", "libssl-dev", "nlohmann-json3-dev",
    ]
    if includes_ibus:
        build += ["libibus-1.0-dev", "librime-dev"]
    if includes_fcitx:
        build += ["libfcitx5core-dev"]
    depends = ["${shlibs:Depends}", "${misc:Depends}", "libgtk-3-0", "libportaudio2"]
    if includes_ibus:
        depends += ["ibus", "librime1", "librime-data", "rime-data-luna-pinyin"]
    if includes_fcitx:
        depends += ["fcitx5"]
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
    build = [
        "BuildRequires:  python3", "BuildRequires:  cmake", "BuildRequires:  gcc-c++",
        "BuildRequires:  pkgconfig", "BuildRequires:  systemd-rpm-macros",
        "BuildRequires:  portaudio-devel", "BuildRequires:  gtk3-devel",
        "BuildRequires:  yaml-cpp-devel", "BuildRequires:  libcurl-devel",
        "BuildRequires:  openssl-devel", "BuildRequires:  nlohmann-json-devel",
    ]
    if includes_ibus:
        build += ["BuildRequires:  ibus-devel", "BuildRequires:  librime-devel"]
    if includes_fcitx:
        build += ["BuildRequires:  fcitx5-devel"]
    requires = ["Requires:       gtk3", "Requires:       portaudio", "Requires:       yaml-cpp"]
    files = [
        "%{_libexecdir}/vocotype-audio-recorder",
        "%{_libexecdir}/vocotype-model-manager",
    ]
    if includes_ibus:
        requires += ["Requires:       ibus", "Requires:       librime", "Requires:       brise"]
        files += [
            "%{_libexecdir}/vocotype-ibus-engine",
            "%{_datadir}/ibus/component/vocotype.xml",
        ]
    if includes_fcitx:
        requires += ["Requires:       fcitx5"]
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
    depends = ["gtk3", "portaudio", "yaml-cpp", "curl", "openssl"]
    makedepends = ["python", "cmake", "gcc", "pkgconf", "nlohmann-json"]
    if includes_ibus:
        depends += ["ibus", "librime", "librime-data"]
    if includes_fcitx:
        depends += ["fcitx5"]
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
