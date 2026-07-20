#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FunASR模型下载脚本
并行下载所有模型文件
"""
import logging
import sys
import json
import threading
from app.funasr_config import MODEL_REVISION, get_models_for_download
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)


def download_model(model_config, progress_callback=None):
    """下载单个模型（使用 modelscope.snapshot_download，无需 funasr/torch）"""
    model_name = model_config["name"]
    model_type = model_config["type"]

    try:
        from modelscope.hub.snapshot_download import snapshot_download

        if progress_callback:
            progress_callback(model_type, "downloading", 0)

        # 下载到本地缓存目录
        snapshot_download(model_name, revision=MODEL_REVISION)

        if progress_callback:
            progress_callback(model_type, "completed", 100)

        return {"success": True, "model": model_type}

    except Exception as e:
        if progress_callback:
            progress_callback(model_type, "error", 0, str(e))
        return {"success": False, "model": model_type, "error": str(e)}

def main():
    """主函数：并行下载所有模型"""
    # 配置日志系统（使用统一配置）
    import os
    project_root = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(project_root, "logs")
    setup_logging("INFO", log_dir)
    
    # 从统一配置获取模型列表
    models = get_models_for_download()
    
    # 进度跟踪
    progress = {"asr": 0, "vad": 0, "punc": 0}
    results = {}
    completed_count = 0
    total_count = len(models)
    count_lock = threading.Lock()  # 添加锁保护计数器
    results_lock = threading.Lock()
    
    def progress_callback(model_type, stage, percent, error=None):
        nonlocal completed_count
        
        # 使用锁保护共享变量的修改
        with count_lock:
            if stage == "downloading":
                progress[model_type] = percent
            elif stage == "completed":
                progress[model_type] = 100
                completed_count += 1
            elif stage == "error":
                progress[model_type] = 0
                completed_count += 1
            
            # 计算总体进度
            overall_progress = sum(progress.values()) / total_count
            current_completed = completed_count
        
        # 输出进度信息（在锁外执行I/O操作）
        status = {
            "stage": stage,
            "model": model_type,
            "progress": percent,
            "overall_progress": round(overall_progress, 1),
            "completed": current_completed,
            "total": total_count
        }
        
        if error:
            status["error"] = error
            
        print(json.dumps(status, ensure_ascii=False))
        sys.stdout.flush()
    
    # 启动并行下载线程
    threads = []
    for model_config in models:
        def worker(config=model_config):
            result = download_model(config, progress_callback)
            with results_lock:
                results[config["type"]] = result

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)
    
    # 等待所有线程完成
    for thread in threads:
        thread.join()
    
    # 检查结果
    failed_models = [model_type for model_type, result in results.items() if not result["success"]]
    
    if failed_models:
        final_result = {
            "success": False,
            "error": f"以下模型下载失败: {', '.join(failed_models)}",
            "failed_models": failed_models,
            "results": results
        }
    else:
        final_result = {
            "success": True,
            "message": "所有模型下载完成",
            "results": results
        }
    
    print(json.dumps(final_result, ensure_ascii=False))
    sys.stdout.flush()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error_result = {
            "success": False,
            "error": str(e)
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)


def get_model_cache_path(
    model_name,
    revision,
    *,
    required_files=(),
    required_any_files=(("model_quant.onnx", "model.onnx"),),
):
    """Return a complete local ModelScope snapshot, downloading missing files.

    ``required_files`` must all exist. For every tuple in
    ``required_any_files``, at least one member must exist. This lets the
    contextual ASR model require ``model_eb.onnx`` while accepting either a
    quantized or unquantized backbone.
    """
    from pathlib import Path
    from modelscope.hub.snapshot_download import snapshot_download

    def is_complete(path: Path) -> bool:
        return (
            path.is_dir()
            and all((path / name).exists() for name in required_files)
            and all(any((path / name).exists() for name in group) for group in required_any_files)
        )

    home = Path.home()
    cache_base = home / ".cache" / "modelscope" / "hub" / "models" / "iic"
    short_name = model_name.split('/')[-1] if '/' in model_name else model_name
    model_dir = cache_base / short_name

    if is_complete(model_dir):
        logger.info("使用本地缓存模型: %s", model_dir)
        return str(model_dir)

    logger.info("本地模型不完整，检查 ModelScope 缓存: %s", model_name)
    try:
        offline_dir = Path(
            snapshot_download(
                model_name,
                revision=revision,
                local_files_only=True,
            )
        )
        if is_complete(offline_dir):
            logger.info("使用已下载的完整模型: %s", offline_dir)
            return str(offline_dir)
    except Exception as offline_error:
        logger.warning("离线模型检查失败: %s", offline_error)

    logger.info("下载缺失的模型文件: %s", model_name)
    downloaded_dir = Path(snapshot_download(model_name, revision=revision))
    if not is_complete(downloaded_dir):
        missing = [name for name in required_files if not (downloaded_dir / name).exists()]
        missing_groups = [
            "/".join(group)
            for group in required_any_files
            if not any((downloaded_dir / name).exists() for name in group)
        ]
        details = ", ".join((*missing, *missing_groups)) or "unknown"
        raise FileNotFoundError(f"模型快照缺少必需文件: {details}: {downloaded_dir}")
    logger.info("模型下载完成: %s", downloaded_dir)
    return str(downloaded_dir)
