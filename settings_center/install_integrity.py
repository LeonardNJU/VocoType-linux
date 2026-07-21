"""Installed-file integrity and mixed-installation probes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from vocotype_version import __version__

KEY_FILES = (
    "vocotype_version.py",
    "app/config.py",
    "app/funasr_server.py",
    "app/streaming_asr.py",
    "app/voice_edit.py",
    "app/slm_polisher.py",
    "fcitx5/backend/fcitx5_server.py",
    "fcitx5/backend/audio_recorder.py",
    "ibus/engine.py",
    "settings_center/application.py",
    "settings_center/config_service.py",
    "settings_center/doctor.py",
    "settings_center/playground_service.py",
    "settings_center/setup_manager.py",
    "settings_center/install_integrity.py",
    "settings_center/version_check.py",
)

FCITX_BINARY_MARKERS = (
    b"PanelStyle",
    b"MinRecordingMs",
    "🎤 录音中...".encode("utf-8"),
    "🟢 正在听".encode("utf-8"),
    b"Suppressed duplicate VoCoType recording start",
)


@dataclass(frozen=True)
class IntegrityReport:
    status: str
    summary: str
    details: str
    reference_version: str
    checked_files: int
    mismatched_files: int
    missing_files: int


@dataclass(frozen=True)
class RuntimeRoot:
    name: str
    path: Path
    kind: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_integrity_manifest(project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    files: dict[str, str] = {}
    for relative in KEY_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"完整性清单缺少源文件：{relative}")
        files[relative] = sha256_file(path)
    return {
        "schema_version": 1,
        "version": __version__,
        "files": files,
        "fcitx_binary_markers": [
            marker.decode("utf-8", errors="strict") for marker in FCITX_BINARY_MARKERS
        ],
    }


def load_integrity_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise ValueError(f"完整性清单格式无效：{path}")
    return payload


def local_reference_manifest(project_root: Path | None) -> dict[str, Any] | None:
    candidates: list[Path] = []
    if project_root is not None:
        root = Path(project_root)
        candidates.extend(
            [root / "data/install-integrity.json", root / "install-integrity.json"]
        )
    runtime_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            runtime_root / "install-integrity.json",
            runtime_root / "data/install-integrity.json",
            Path("/usr/share/vocotype/data/install-integrity.json"),
        ]
    )
    for manifest_path in dict.fromkeys(candidates):
        if not manifest_path.is_file():
            continue
        try:
            return load_integrity_manifest(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    if project_root is not None:
        try:
            return build_integrity_manifest(Path(project_root))
        except (OSError, ValueError):
            pass
    return None


def installed_runtime_roots(
    *,
    home: Path | None = None,
    system_prefix: Path = Path("/usr"),
) -> tuple[RuntimeRoot, ...]:
    user_home = home or Path.home()
    candidates = (
        RuntimeRoot(
            "Fcitx 用户运行时",
            user_home / ".local/share/vocotype-fcitx5",
            "fcitx-user",
        ),
        RuntimeRoot(
            "IBus 用户运行时",
            user_home / ".local/share/vocotype",
            "ibus-user",
        ),
        RuntimeRoot(
            "系统软件包运行时",
            system_prefix / "share/vocotype",
            "system",
        ),
    )
    result: list[RuntimeRoot] = []
    for item in candidates:
        if not item.path.is_dir():
            continue
        if item.kind == "system" and not (
            (item.path / "vocotype_version.py").is_file()
            or (item.path / ".system-package").is_file()
            or (item.path / "app").is_dir()
        ):
            continue
        result.append(item)
    return tuple(result)


def _installed_path(root: RuntimeRoot, relative: str) -> Path | None:
    if root.kind == "system":
        return root.path / relative
    if relative == "vocotype_version.py":
        return root.path / relative
    if relative.startswith("app/") or relative.startswith("settings_center/"):
        return root.path / relative
    if root.kind == "fcitx-user" and relative.startswith("fcitx5/backend/"):
        return root.path / "backend" / Path(relative).name
    if root.kind == "ibus-user" and relative.startswith("ibus/"):
        return root.path / relative
    return None


def _read_installed_version(root: RuntimeRoot) -> str:
    path = root.path / "vocotype_version.py"
    if not path.is_file():
        return "missing"
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        path.read_text(encoding="utf-8", errors="replace"),
        re.MULTILINE,
    )
    return match.group(1) if match else "unknown"


def _module_candidates(home: Path, system_prefix: Path) -> tuple[Path, ...]:
    system_lib = system_prefix / "lib"
    values = [
        home / ".local/lib/fcitx5/vocotype.so",
        home / ".local/lib64/fcitx5/vocotype.so",
        system_lib / "fcitx5/vocotype.so",
        system_prefix / "lib64/fcitx5/vocotype.so",
        *sorted(system_lib.glob("*/fcitx5/vocotype.so")),
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for path in values:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return tuple(result)


def probe_installation_integrity(
    reference_manifest: Mapping[str, Any] | None,
    *,
    home: Path | None = None,
    system_prefix: Path = Path("/usr"),
) -> IntegrityReport:
    user_home = home or Path.home()
    roots = installed_runtime_roots(home=user_home, system_prefix=system_prefix)
    if reference_manifest is None:
        return IntegrityReport(
            "info",
            "没有可用的参考清单",
            "当前环境无法定位源码树或随包完整性清单。",
            "unknown",
            0,
            0,
            0,
        )
    files = reference_manifest.get("files", {})
    if not isinstance(files, Mapping):
        raise ValueError("参考完整性清单缺少 files")
    reference_version = str(reference_manifest.get("version", "unknown"))
    if not roots:
        return IntegrityReport(
            "info",
            "未发现已安装运行时",
            "没有用户级或系统级 VoCoType 运行时可供对照。",
            reference_version,
            0,
            0,
            0,
        )

    checked = 0
    mismatched = 0
    missing = 0
    detail_lines: list[str] = []
    versions: dict[str, str] = {}
    for root in roots:
        version = _read_installed_version(root)
        versions[root.name] = version
        detail_lines.append(f"[{root.name}] {root.path} (version={version})")
        for relative, expected in files.items():
            installed = _installed_path(root, str(relative))
            if installed is None:
                continue
            checked += 1
            if not installed.is_file():
                missing += 1
                detail_lines.append(f"  MISSING {relative}: {installed}")
                continue
            actual = sha256_file(installed)
            if actual != str(expected):
                mismatched += 1
                detail_lines.append(
                    f"  MISMATCH {relative}: expected={str(expected)[:12]} "
                    f"actual={actual[:12]} path={installed}"
                )

    modules = _module_candidates(user_home, system_prefix)
    for module in modules:
        checked += 1
        payload = module.read_bytes()
        missing_markers = [
            marker.decode("utf-8", errors="replace")
            for marker in FCITX_BINARY_MARKERS
            if marker not in payload
        ]
        if missing_markers:
            mismatched += 1
            detail_lines.append(
                f"  MISMATCH Fcitx module {module}: 缺少能力标记 "
                + ", ".join(missing_markers)
            )
        else:
            detail_lines.append(f"  OK Fcitx module capability markers: {module}")

    residue_paths = (
        user_home / ".local/share/fcitx5/inputmethod/vocotype.conf",
        user_home / ".config/environment.d/fcitx5-vocotype.conf",
        system_prefix / "share/vocotype/.source-fcitx-integration",
    )
    residues = [path for path in residue_paths if path.exists()]
    for path in residues:
        detail_lines.append(f"  LEGACY 残留：{path}")

    version_values = {value for value in versions.values() if value not in {"missing", "unknown"}}
    version_mixed = len(version_values) > 1
    version_outdated = any(
        value not in {reference_version, "missing", "unknown"}
        for value in versions.values()
    )

    if mismatched or missing or version_mixed:
        status = "fail"
        summary = (
            f"安装内容不一致：{mismatched} 个哈希/能力不匹配，"
            f"{missing} 个关键文件缺失"
        )
    elif residues or version_outdated or len(modules) > 1:
        status = "warn"
        reasons: list[str] = []
        if version_outdated:
            reasons.append("安装版本与参考版本不同")
        if len(modules) > 1:
            reasons.append("同时存在多个 Fcitx module")
        if residues:
            reasons.append("发现旧版残留")
        summary = "；".join(reasons)
    else:
        status = "pass"
        summary = f"{checked} 个关键文件/能力标记与参考版本一致"

    return IntegrityReport(
        status,
        summary,
        "\n".join(detail_lines),
        reference_version,
        checked,
        mismatched,
        missing,
    )
