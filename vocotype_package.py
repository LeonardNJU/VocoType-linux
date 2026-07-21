"""Native-package flavor metadata and marker parsing."""

from __future__ import annotations

from pathlib import Path

PACKAGE_FLAVORS: dict[str, dict[str, object]] = {
    "universal": {
        "package_name": "vocotype-linux",
        "title": "IBus and Fcitx 5",
        "summary": "Offline voice input for IBus and Fcitx 5",
        "includes_ibus": True,
        "includes_fcitx5": True,
    },
    "ibus": {
        "package_name": "vocotype-linux-ibus",
        "title": "IBus",
        "summary": "Offline voice input for IBus",
        "includes_ibus": True,
        "includes_fcitx5": False,
    },
    "fcitx5": {
        "package_name": "vocotype-linux-fcitx5",
        "title": "Fcitx 5",
        "summary": "Offline voice input for Fcitx 5",
        "includes_ibus": False,
        "includes_fcitx5": True,
    },
}

_FLAVOR_ALIASES = {
    "fcitx": "fcitx5",
    "all": "universal",
    "both": "universal",
}


def package_flavor_metadata(value: str) -> dict[str, object]:
    key = str(value or "").strip().lower()
    key = _FLAVOR_ALIASES.get(key, key)
    if key not in PACKAGE_FLAVORS:
        raise ValueError(f"unknown package flavor: {value}")
    result = dict(PACKAGE_FLAVORS[key])
    result["flavor"] = key
    result["conflicts"] = [
        str(item["package_name"])
        for flavor, item in PACKAGE_FLAVORS.items()
        if flavor != key
    ]
    return result


def read_system_package_marker(path: Path) -> dict[str, str]:
    marker = Path(path)
    if not marker.is_file():
        return {}
    try:
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    result: dict[str, str] = {}
    for raw in lines:
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    if not result:
        return {}

    flavor = result.get("flavor", "universal").lower()
    if flavor not in PACKAGE_FLAVORS:
        flavor = "universal"
    result["flavor"] = flavor
    result.setdefault("package", str(PACKAGE_FLAVORS[flavor]["package_name"]))
    return result
