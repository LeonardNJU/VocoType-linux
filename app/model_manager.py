#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型管理模块
负责检测模型是否存在、提示用户下载、自动下载模型
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Callable

from app.funasr_config import MODEL_REVISION, get_models_for_download

logger = logging.getLogger(__name__)


def get_model_cache_path(model_name: str) -> Path:
    """获取模型本地缓存路径"""
    home = Path.home()
    short_name = model_name.split('/')[-1] if '/' in model_name else model_name
    return home / ".cache" / "modelscope" / "hub" / "models" / "iic" / short_name


def check_model_exists(model_name: str) -> bool:
    """检查单个模型是否已下载"""
    model_dir = get_model_cache_path(model_name)
    if not model_dir.exists():
        return False

    # 检查是否有模型文件
    quant_file = model_dir / "model_quant.onnx"
    base_file = model_dir / "model.onnx"

    return quant_file.exists() or base_file.exists()


def check_all_models() -> Dict[str, bool]:
    """检查所有模型是否存在

    Returns:
        Dict[str, bool]: 模型类型 -> 是否存在
    """
    models = get_models_for_download()
    result = {}
    for model_config in models:
        model_type = model_config["type"]
        model_name = model_config["name"]
        result[model_type] = check_model_exists(model_name)
    return result


def get_missing_models() -> List[Dict]:
    """获取未下载的模型列表"""
    models = get_models_for_download()
    missing = []
    for model_config in models:
        if not check_model_exists(model_config["name"]):
            missing.append(model_config)
    return missing


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def estimate_total_size() -> str:
    """估算模型总大小"""
    # 根据模型历史数据估算
    # ASR: ~300MB, VAD: ~50MB, Punctuation: ~150MB
    return "约 500MB"


def show_gui_prompt(title: str, message: str) -> bool:
    """显示图形界面提示，返回用户是否同意

    优先使用 zenity (GNOME/GTK) 或 kdialog (KDE)，
    如果没有则返回 None，调用方应使用命令行提示
    """
    import shutil
    import subprocess

    # 尝试 zenity (GNOME/GTK)
    if shutil.which("zenity"):
        try:
            result = subprocess.run(
                ["zenity", "--question",
                 "--title", title,
                 "--text", message,
                 "--ok-label", "下载",
                 "--cancel-label", "取消"],
                capture_output=True,
                timeout=60
            )
            return result.returncode == 0
        except Exception:
            pass

    # 尝试 kdialog (KDE)
    if shutil.which("kdialog"):
        try:
            result = subprocess.run(
                ["kdialog", "--title", title,
                 "--yesno", message,
                 "--yes-label", "下载",
                 "--no-label", "取消"],
                capture_output=True,
                timeout=60
            )
            return result.returncode == 0
        except Exception:
            pass

    return None


def show_gui_progress(title: str = "下载语音模型") -> Optional[Callable]:
    """显示图形下载进度，返回更新进度的回调函数"""
    import shutil
    import subprocess

    # 尝试 zenity 进度对话框
    if shutil.which("zenity"):
        try:
            proc = subprocess.Popen(
                ["zenity", "--progress",
                 "--title", title,
                 "--text", "正在下载模型...",
                 "--percentage", "0",
                 "--auto-close",
                 "--no-cancel"],
                stdin=subprocess.PIPE,
                text=True
            )

            def update_progress(percent: int, message: str = ""):
                try:
                    if proc.poll() is None:
                        proc.stdin.write(f"{percent}\n")
                        if message:
                            proc.stdin.write(f"# {message}\n")
                        proc.stdin.flush()
                except Exception:
                    pass

            return update_progress
        except Exception:
            pass

    return None


def prompt_download_interactive() -> bool:
    """交互式提示用户是否下载模型（命令行方式）"""
    print("\n" + "=" * 60)
    print("🎤 VoCoType 语音输入法 - 首次使用设置")
    print("=" * 60)
    print()
    print("检测到语音识别模型尚未下载。")
    print()
    print("模型信息:")
    print("  • ASR 模型 (语音识别): Paraformer-large")
    print("  • VAD 模型 (语音活动检测): FSMN")
    print("  • Punctuation 模型 (标点预测): CT-Transformer")
    print()
    print(f"总大小: {estimate_total_size()}")
    print("下载源: ModelScope (阿里云)")
    print()
    print("注意: 所有模型下载后在本地运行，无需联网即可使用。")
    print()

    while True:
        try:
            choice = input("是否现在下载模型? [Y/n]: ").strip().lower()
            if choice in ('', 'y', 'yes'):
                return True
            elif choice in ('n', 'no'):
                return False
            else:
                print("请输入 Y 或 n")
        except EOFError:
            # 非交互式环境，默认不下载
            return False
        except KeyboardInterrupt:
            print("\n已取消")
            return False


def download_with_progress(
    progress_callback: Optional[Callable] = None,
    console_output: bool = True
) -> Dict:
    """下载模型并显示进度

    Args:
        progress_callback: 图形界面进度回调函数
        console_output: 是否输出到控制台

    Returns:
        Dict: 下载结果
    """
    from app.download_models import download_model, get_models_for_download

    models = get_models_for_download()
    total = len(models)
    completed = 0
    failed = []

    def internal_callback(model_type: str, stage: str, percent: int, error: str = None):
        # 计算总体进度
        nonlocal completed

        if stage == "completed":
            completed += 1
        elif stage == "error":
            completed += 1
            failed.append(model_type)

        overall = int((completed / total) * 100)

        # 构建状态消息
        if stage == "downloading":
            status_msg = f"正在下载 {model_type.upper()} 模型 ({percent}%)..."
        elif stage == "completed":
            status_msg = f"✓ {model_type.upper()} 模型下载完成"
        elif stage == "error":
            status_msg = f"✗ {model_type.upper()} 模型下载失败: {error}"
        else:
            status_msg = f"{model_type.upper()}: {stage}"

        # 更新图形进度
        if progress_callback:
            progress_callback(overall, status_msg)

        # 输出到控制台
        if console_output:
            print(f"[{overall}%] {status_msg}")

    # 开始下载
    if console_output:
        print("\n开始下载模型...\n")

    results = {}
    for model_config in models:
        result = download_model(model_config, internal_callback)
        results[model_config["type"]] = result

    # 检查结果
    if failed:
        return {
            "success": False,
            "error": f"以下模型下载失败: {', '.join(failed)}",
            "failed_models": failed,
            "results": results
        }

    return {
        "success": True,
        "message": "所有模型下载完成",
        "results": results
    }


def ensure_models(
    use_gui: bool = True,
    force_prompt: bool = False,
    silent: bool = False
) -> bool:
    """确保模型已下载，如果没有则提示用户下载

    Args:
        use_gui: 是否尝试使用图形界面提示
        force_prompt: 即使模型存在也提示（用于重新下载）
        silent: 静默模式，不提示，只检查

    Returns:
        bool: 模型是否可用
    """
    # 检查模型状态
    model_status = check_all_models()
    all_exist = all(model_status.values())

    if all_exist and not force_prompt:
        logger.info("所有模型已存在，跳过下载检查")
        return True

    if silent:
        # 静默模式，只返回状态
        return all_exist

    # 获取缺失的模型
    missing = get_missing_models()
    if not missing and not force_prompt:
        return True

    # 构建提示信息
    missing_types = [m["type"] for m in missing]
    logger.info(f"缺失模型: {missing_types}")

    # 尝试图形界面提示
    user_agreed = None
    if use_gui:
        message = (
            f"VoCoType 需要下载语音识别模型才能使用。\n\n"
            f"缺失模型: {', '.join(missing_types)}\n"
            f"总大小: {estimate_total_size()}\n\n"
            f"模型下载后在本地运行，无需联网即可使用。\n\n"
            f"是否现在下载?"
        )
        user_agreed = show_gui_prompt("VoCoType - 下载语音模型", message)

    # 如果图形界面不可用或用户取消，使用命令行提示
    if user_agreed is None:
        user_agreed = prompt_download_interactive()

    if not user_agreed:
        print("\n已取消下载。模型下载后才能使用语音输入功能。")
        print("您可以稍后手动运行: python -m app.download_models")
        return False

    # 开始下载
    print("\n")

    # 尝试图形进度
    progress_callback = show_gui_progress() if use_gui else None

    try:
        result = download_with_progress(
            progress_callback=progress_callback,
            console_output=True
        )

        if result["success"]:
            print("\n✓ 模型下载完成！VoCoType 现在可以使用了。\n")
            return True
        else:
            print(f"\n✗ 模型下载失败: {result.get('error', '未知错误')}")
            print("请检查网络连接后重试。")
            return False

    except Exception as e:
        logger.error(f"下载模型时出错: {e}")
        print(f"\n✗ 下载出错: {e}")
        return False


if __name__ == "__main__":
    # 测试模块
    logging.basicConfig(level=logging.INFO)

    # 检查模型状态
    print("检查模型状态...")
    status = check_all_models()
    for model_type, exists in status.items():
        status_icon = "✓" if exists else "✗"
        print(f"  {status_icon} {model_type}: {'已下载' if exists else '未下载'}")

    # 确保模型存在
    print("\n")
    success = ensure_models(use_gui=True)
    sys.exit(0 if success else 1)
