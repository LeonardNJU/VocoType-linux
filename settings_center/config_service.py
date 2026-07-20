"""Unified, atomic configuration access for the VoCoType settings center."""

from __future__ import annotations

import configparser
import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.config import DEFAULT_CONFIG, _merge_dict

FCITX_CONFIG_FILENAME = "fcitx5-backend.json"
IBUS_CONFIG_FILENAME = "ibus.json"
TERMS_FILENAME = "terms.yaml"
LEGACY_TERMS_FILENAME = "user-dictionary.yaml"
FCITX_MODULE_CONFIG_FILENAME = "vocotype.conf"
AUDIO_CONFIG_FILENAME = "audio.conf"


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()


def vocotype_config_dir() -> Path:
    return xdg_config_home() / "vocotype"


def fcitx_backend_path() -> Path:
    return vocotype_config_dir() / FCITX_CONFIG_FILENAME


def ibus_config_path() -> Path:
    return vocotype_config_dir() / IBUS_CONFIG_FILENAME


def terms_path() -> Path:
    preferred = vocotype_config_dir() / TERMS_FILENAME
    legacy = vocotype_config_dir() / LEGACY_TERMS_FILENAME
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def fcitx_module_config_path() -> Path:
    return xdg_config_home() / "fcitx5" / "conf" / FCITX_MODULE_CONFIG_FILENAME


def audio_config_path() -> Path:
    return vocotype_config_dir() / AUDIO_CONFIG_FILENAME


def load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"配置文件顶层必须是对象: {path}")
    return value


def load_runtime_config() -> dict[str, Any]:
    """Load the most complete runtime config and merge it with defaults."""

    merged = copy.deepcopy(DEFAULT_CONFIG)
    # IBus first, Fcitx second: the Fcitx backend is the primary current UI
    # target, while saving keeps both files synchronized.
    for path in (ibus_config_path(), fcitx_backend_path()):
        try:
            merged = _merge_dict(merged, load_json_mapping(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return merged


def atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def save_runtime_config(config: Mapping[str, Any]) -> tuple[Path, Path]:
    """Atomically synchronize IBus and Fcitx runtime configurations."""

    payload = copy.deepcopy(dict(config))
    for path in (ibus_config_path(), fcitx_backend_path()):
        atomic_write_json(path, payload)
    return ibus_config_path(), fcitx_backend_path()


def update_runtime_sections(sections: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    config = load_runtime_config()
    for name, values in sections.items():
        current = config.get(name)
        if not isinstance(current, dict):
            current = {}
        current.update(dict(values))
        config[name] = current
    save_runtime_config(config)
    return config


def load_audio_config() -> dict[str, Any]:
    path = audio_config_path()
    if not path.exists():
        return {"device_name": "", "device_id": None, "sample_rate": 0}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    if not parser.has_section("audio"):
        raise ValueError(f"音频配置缺少 [audio] section: {path}")
    device_id = parser.get("audio", "device_id", fallback="").strip()
    return {
        "device_name": parser.get("audio", "device_name", fallback="").strip(),
        "device_id": int(device_id) if device_id.isdigit() else None,
        "sample_rate": parser.getint("audio", "sample_rate", fallback=0),
    }


def save_audio_config(*, device_name: str, device_id: int, sample_rate: int) -> Path:
    path = audio_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("[audio]\n")
            safe_name = " ".join(device_name.strip().splitlines())
            handle.write(f"device_name = {safe_name}\n")
            handle.write(f"device_id = {int(device_id)}\n")
            handle.write(f"sample_rate = {max(8000, int(sample_rate))}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return path


def load_fcitx_module_config() -> dict[str, str]:
    path = fcitx_module_config_path()
    if not path.exists():
        return {}
    # Fcitx RawConfig is an INI-like key/value file without a required section.
    parser = configparser.ConfigParser(interpolation=None)
    content = path.read_text(encoding="utf-8")
    parser.read_string("[vocotype]\n" + content)
    return dict(parser["vocotype"])


def save_fcitx_module_config(values: Mapping[str, Any]) -> Path:
    path = fcitx_module_config_path()
    existing = load_fcitx_module_config()
    for key, value in values.items():
        if isinstance(value, bool):
            existing[key.lower()] = "True" if value else "False"
        else:
            existing[key.lower()] = str(value)
    preferred_order = [
        "pttkey",
        "pttholdthresholdms",
        "longmodemodifier",
        "polishbydefault",
        "polishminchars",
        "polishtimeoutms",
        "enablethinking",
        "blockwhencomposing",
        "striptrailingperiodoncommit",
    ]
    ordered = []
    seen = set()
    for key in preferred_order:
        if key in existing:
            ordered.append((key, existing[key]))
            seen.add(key)
    ordered.extend(sorted((key, value) for key, value in existing.items() if key not in seen))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key, value in ordered:
                canonical = {
                    "pttkey": "PTTKey",
                    "pttholdthresholdms": "PTTHoldThresholdMs",
                    "longmodemodifier": "LongModeModifier",
                    "polishbydefault": "PolishByDefault",
                    "polishminchars": "PolishMinChars",
                    "polishtimeoutms": "PolishTimeoutMs",
                    "enablethinking": "EnableThinking",
                    "blockwhencomposing": "BlockWhenComposing",
                    "striptrailingperiodoncommit": "StripTrailingPeriodOnCommit",
                }.get(key, key)
                handle.write(f"{canonical}={value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return path


def ensure_terms_template(template: str) -> Path:
    path = terms_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template, encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def sanitize_config(value: Any) -> Any:
    """Return a recursively redacted copy suitable for diagnostics."""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(token in lowered for token in ("key", "token", "secret", "password", "authorization")):
                result[str(key)] = "<redacted>" if item else ""
            else:
                result[str(key)] = sanitize_config(item)
        return result
    if isinstance(value, list):
        return [sanitize_config(item) for item in value]
    return value
