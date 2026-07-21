#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download and verify every FunASR model required by VoCoType."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from app.funasr_config import MODEL_REVISION, get_models_for_download
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)

_PROXY_ENV_NAMES = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "socks_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "SOCKS_PROXY",
)
_DOWNLOAD_ENV_LOCK = threading.Lock()


@contextmanager
def _without_proxy_environment():
    saved = {name: os.environ[name] for name in _PROXY_ENV_NAMES if name in os.environ}
    try:
        for name in _PROXY_ENV_NAMES:
            os.environ.pop(name, None)
        yield
    finally:
        for name in _PROXY_ENV_NAMES:
            os.environ.pop(name, None)
        os.environ.update(saved)


def _snapshot_download_with_direct_retry(
    snapshot_download: Callable[..., str],
    model_name: str,
    **kwargs: Any,
) -> str:
    """Try the current environment first, then retry once without proxies.

    ModelScope reads proxy configuration from process-wide environment
    variables. Network downloads are therefore serialized so one fallback
    cannot remove proxy variables while another download is still running.
    """

    if kwargs.get("local_files_only"):
        return snapshot_download(model_name, **kwargs)

    with _DOWNLOAD_ENV_LOCK:
        proxy_present = any(os.environ.get(name) for name in _PROXY_ENV_NAMES)
        try:
            return snapshot_download(model_name, **kwargs)
        except Exception as proxy_error:
            if not proxy_present:
                raise
            logger.warning("模型下载使用当前代理失败，改用直连重试: %s", proxy_error)
            try:
                with _without_proxy_environment():
                    return snapshot_download(model_name, **kwargs)
            except Exception as direct_error:
                raise RuntimeError(
                    "ModelScope 下载失败；当前代理尝试错误: "
                    f"{proxy_error}; 无代理直连尝试错误: {direct_error}"
                ) from direct_error


def model_requirements(model_config: dict[str, str]) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    model_type = model_config["type"]
    model_name = model_config["name"].lower()
    if model_type == "asr":
        required = ["config.yaml", "am.mvn", "tokens.json"]
        if "contextual" in model_name or "seaco" in model_name:
            required.append("model_eb.onnx")
        required_any = (("model_quant.onnx", "model.onnx"),)
    elif model_type == "asr_streaming":
        required = ["config.yaml", "am.mvn", "tokens.json"]
        required_any = (
            ("model_quant.onnx", "model.onnx"),
            ("decoder_quant.onnx", "decoder.onnx"),
        )
    elif model_type == "vad":
        required = ["config.yaml", "am.mvn"]
    elif model_type == "punc":
        required = ["config.yaml", "tokens.json"]
        required_any = (("model_quant.onnx", "model.onnx"),)
    else:
        raise ValueError(f"未知模型类型: {model_type}")
    if model_type == "vad":
        required_any = (("model_quant.onnx", "model.onnx"),)
    return tuple(required), required_any


def _is_complete(
    path: Path,
    required_files: tuple[str, ...],
    required_any_files: tuple[tuple[str, ...], ...],
) -> bool:
    return not missing_required_files(path, required_files, required_any_files)


def missing_required_files(
    path: Path,
    required_files: tuple[str, ...],
    required_any_files: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    """Return missing/empty payload names for one model snapshot."""

    if not path.is_dir():
        return ("<model-directory>",)
    missing = [
        name
        for name in required_files
        if not (path / name).is_file() or (path / name).stat().st_size <= 0
    ]
    missing.extend(
        "/".join(group)
        for group in required_any_files
        if not any(
            (path / name).is_file() and (path / name).stat().st_size > 0
            for name in group
        )
    )
    return tuple(missing)


def configured_model_cache_path(model_name: str, *, home: Path | None = None) -> Path:
    """Return ModelScope's conventional cache path for a configured model."""

    if "/" in model_name:
        namespace, short_name = model_name.split("/", 1)
    else:
        namespace, short_name = "iic", model_name
    return (
        (home or Path.home())
        / ".cache"
        / "modelscope"
        / "hub"
        / "models"
        / namespace
        / short_name
    )


def inspect_required_models(*, home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Inspect all configured models without performing network access."""

    status: dict[str, dict[str, Any]] = {}
    for model_config in get_models_for_download():
        required_files, required_any_files = model_requirements(model_config)
        model_path = configured_model_cache_path(model_config["name"], home=home)
        missing = missing_required_files(model_path, required_files, required_any_files)
        status[model_config["type"]] = {
            "name": model_config["name"],
            "path": str(model_path),
            "complete": not missing,
            "missing": list(missing),
        }
    return status


def get_model_cache_path(
    model_name: str,
    revision: str,
    *,
    required_files: tuple[str, ...] = (),
    required_any_files: tuple[tuple[str, ...], ...] = (("model_quant.onnx", "model.onnx"),),
) -> str:
    """Return a complete local ModelScope snapshot, downloading if needed."""

    from modelscope.hub.snapshot_download import snapshot_download

    model_dir = configured_model_cache_path(model_name)
    if _is_complete(model_dir, required_files, required_any_files):
        logger.info("使用本地完整模型: %s", model_dir)
        return str(model_dir)

    logger.info("本地模型不完整，检查 ModelScope 缓存: %s", model_name)
    try:
        offline_dir = Path(
            _snapshot_download_with_direct_retry(
                snapshot_download,
                model_name,
                revision=revision,
                local_files_only=True,
            )
        )
        if _is_complete(offline_dir, required_files, required_any_files):
            logger.info("使用已下载的完整模型: %s", offline_dir)
            return str(offline_dir)
    except Exception as offline_error:
        logger.warning("离线模型检查失败: %s", offline_error)

    logger.info("下载缺失的模型文件: %s", model_name)
    downloaded_dir = Path(
        _snapshot_download_with_direct_retry(
            snapshot_download,
            model_name,
            revision=revision,
        )
    )
    if not _is_complete(downloaded_dir, required_files, required_any_files):
        missing = list(missing_required_files(downloaded_dir, required_files, required_any_files))
        raise FileNotFoundError(
            f"模型快照下载后仍不完整，缺少: {', '.join(missing) or 'unknown'}: {downloaded_dir}"
        )
    logger.info("模型下载并校验完成: %s", downloaded_dir)
    return str(downloaded_dir)


def download_model(
    model_config: dict[str, str],
    progress_callback: Callable[[str, str, int, str | None], None] | None = None,
) -> dict[str, Any]:
    model_name = model_config["name"]
    model_type = model_config["type"]
    try:
        if progress_callback:
            progress_callback(model_type, "checking", 0, None)
        required_files, required_any_files = model_requirements(model_config)
        path = get_model_cache_path(
            model_name,
            MODEL_REVISION,
            required_files=required_files,
            required_any_files=required_any_files,
        )
        if progress_callback:
            progress_callback(model_type, "completed", 100, None)
        return {"success": True, "model": model_type, "path": path}
    except Exception as exc:
        if progress_callback:
            progress_callback(model_type, "error", 0, str(exc))
        return {"success": False, "model": model_type, "error": str(exc)}


def download_all_models(
    progress_callback: Callable[[str, str, int, str | None], None] | None = None,
) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    for model_config in get_models_for_download():
        result = download_model(model_config, progress_callback)
        results[model_config["type"]] = result
    failed = [name for name, result in results.items() if not result["success"]]
    if failed:
        return {
            "success": False,
            "error": f"以下必需模型下载或校验失败: {', '.join(failed)}",
            "failed_models": failed,
            "results": results,
        }
    return {"success": True, "message": "ASR、VAD、标点模型均已下载并校验", "results": results}


def main() -> int:
    setup_logging("INFO", None)
    completed = 0
    total = len(get_models_for_download())

    def report(model_type: str, stage: str, percent: int, error: str | None = None) -> None:
        nonlocal completed
        if stage in {"completed", "error"}:
            completed += 1
        payload: dict[str, Any] = {
            "stage": stage,
            "model": model_type,
            "progress": percent,
            "completed": completed,
            "total": total,
        }
        if error:
            payload["error"] = error
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    result = download_all_models(report)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
