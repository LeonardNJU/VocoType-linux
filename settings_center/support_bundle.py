"""Privacy-conscious support bundle generation."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from vocotype_version import __version__

from .config_service import (
    fcitx_backend_path,
    fcitx_module_config_path,
    load_audio_config,
    ibus_config_path,
    load_json_mapping,
    sanitize_config,
    terms_path,
)
from .doctor import doctor_summary, run_doctor

MAX_LOG_BYTES = 2 * 1024 * 1024
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization|api[_-]?key|token|secret|password)(\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)
_TRANSCRIPT_LOG_PATTERNS = (
    re.compile(r"(?m)^.*(?:ASR识别完成，原始文本|转录完成，最终文本|已提交文本|Rime 提交文本):.*$"),
    re.compile(r"(?m)^.*SURROUNDING_PROBE.*$"),
    re.compile(r"(?m)^.*编辑模式命中.*(?:instruction|rewritten)=.*$"),
    re.compile(r"(?m)^.*候选\s+\d+:.*$"),
)


def _redact_text(text: str) -> str:
    result = text
    # Redact complete Bearer credentials before the generic key/value pattern;
    # otherwise only the word “Bearer” would be consumed and the token remain.
    result = _SECRET_PATTERNS[1].sub("Bearer <redacted>", result)
    result = _SECRET_PATTERNS[2].sub("sk-<redacted>", result)
    result = _SECRET_PATTERNS[0].sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        result,
    )
    for pattern in _TRANSCRIPT_LOG_PATTERNS:
        result = pattern.sub("[VoCoType user text redacted]", result)
    return result


def _run(argv: list[str], timeout: float = 10.0) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:  # noqa: BLE001
        return f"command failed: {exc}\n"
    return f"$ {' '.join(argv)}\nexit={result.returncode}\n{result.stdout}{result.stderr}"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_redact_text(text), encoding="utf-8", errors="replace")


def _copy_tail(source: Path, target: Path, limit: int = MAX_LOG_BYTES) -> None:
    try:
        size = source.stat().st_size
        with source.open("rb") as handle:
            if size > limit:
                handle.seek(-limit, os.SEEK_END)
            data = handle.read(limit)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_redact_text(data.decode("utf-8", errors="replace")), encoding="utf-8")
    except OSError:
        return


def default_bundle_path() -> Path:
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return base / f"vocotype-support-{stamp}.tar.gz"


def create_support_bundle(output_path: str | os.PathLike[str] | None = None) -> Path:
    """Create a bundle without raw audio, API credentials, or dictionary contents."""

    target = Path(output_path).expanduser() if output_path else default_bundle_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    checks = run_doctor(include_slm_probe=False)

    with tempfile.TemporaryDirectory(prefix="vocotype-support-") as temp_dir:
        root = Path(temp_dir) / "vocotype-support"
        root.mkdir()
        metadata = {
            "version": __version__,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "platform": platform.platform(),
            "python": sys.version,
            "python_executable": sys.executable,
            "doctor_summary": doctor_summary(checks),
            "privacy": {
                "raw_audio_included": False,
                "api_credentials_included": False,
                "dictionary_contents_included": False,
            },
        }
        _write_text(root / "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        _write_text(
            root / "doctor.json",
            json.dumps([asdict(item) for item in checks], ensure_ascii=False, indent=2) + "\n",
        )
        _write_text(
            root / "PRIVACY.txt",
            "This bundle excludes raw audio, API credentials, and dictionary contents.\n"
            "Known transcript-bearing log lines are redacted. Other application or system logs\n"
            "may still contain paths, hostnames, usernames, or context; review before sharing.\n",
        )

        for name, path in {
            "fcitx5-backend.json": fcitx_backend_path(),
            "ibus.json": ibus_config_path(),
        }.items():
            if not path.exists():
                continue
            try:
                config = sanitize_config(load_json_mapping(path))
                _write_text(root / "config" / name, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            except Exception as exc:  # noqa: BLE001
                _write_text(root / "config" / f"{name}.error.txt", str(exc))

        try:
            _write_text(
                root / "config" / "audio.json",
                json.dumps(load_audio_config(), ensure_ascii=False, indent=2) + "\n",
            )
        except Exception as exc:  # noqa: BLE001
            _write_text(root / "config" / "audio.error.txt", str(exc))

        module_config = fcitx_module_config_path()
        if module_config.exists():
            _copy_tail(module_config, root / "config" / "fcitx-module.conf", 256 * 1024)
        dictionary = terms_path()
        if dictionary.exists():
            stat = dictionary.stat()
            _write_text(
                root / "config" / "terms-metadata.json",
                json.dumps({"path": str(dictionary), "size": stat.st_size, "mtime": stat.st_mtime}, indent=2) + "\n",
            )

        commands = {
            "systemctl-status.txt": ["systemctl", "--user", "status", "vocotype-fcitx5-backend.service", "--no-pager"],
            "journal.txt": ["journalctl", "--user", "-u", "vocotype-fcitx5-backend.service", "-b", "--no-pager", "-n", "1000"],
            "fcitx5-diagnose.txt": ["fcitx5-diagnose"],
        }
        for filename, argv in commands.items():
            if shutil.which(argv[0]):
                _write_text(root / "commands" / filename, _run(argv))

        log_candidates = [
            Path.home() / ".local/share/vocotype/ibus.log",
            Path.home() / ".local/share/vocotype-fcitx5/fcitx5-backend.log",
            Path.home() / ".local/share/vocotype-fcitx5/logs/fcitx5-backend.log",
        ]
        for candidate in log_candidates:
            if candidate.is_file():
                _copy_tail(candidate, root / "logs" / candidate.name)

        with tarfile.open(target, "w:gz") as archive:
            archive.add(root, arcname=root.name)
    os.chmod(target, 0o600)
    return target
