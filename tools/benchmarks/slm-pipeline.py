#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark ASR-only vs ASR+SLM(long mode) pipeline overhead.

Usage example:
  python tools/benchmarks/slm-pipeline.py samples/*.wav \
    --repeat 5 \
    --slm-model Qwen/Qwen3.5-0.8B \
    --slm-endpoint http://127.0.0.1:18080/v1/chat/completions \
    --output-json /tmp/vocotype-benchmark.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.funasr_server import FunASRServer
from app.slm_polisher import SLMPolisher

logger = logging.getLogger("benchmark_slm_pipeline")


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)

    sorted_values = sorted(values)
    idx = (len(sorted_values) - 1) * (p / 100.0)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = idx - lower
    return sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac


def _format_ms_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {
            "mean": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "min": 0.0,
        }

    return {
        "mean": round(statistics.mean(values), 2),
        "p50": round(_percentile(values, 50), 2),
        "p90": round(_percentile(values, 90), 2),
        "p95": round(_percentile(values, 95), 2),
        "max": round(max(values), 2),
        "min": round(min(values), 2),
    }


def _read_proc_status_value(pid: int, key: str) -> Optional[int]:
    """Read kB value from /proc/<pid>/status, e.g. VmRSS / VmHWM."""
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return None

    try:
        for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(f"{key}:"):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])
    except Exception:  # noqa: BLE001
        return None
    return None


def _read_proc_cpu_jiffies(pid: int) -> Optional[int]:
    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.exists():
        return None

    try:
        data = stat_path.read_text(encoding="utf-8", errors="ignore").split()
        # utime=14th, stime=15th field (1-based), 13/14 in 0-based index.
        utime = int(data[13])
        stime = int(data[14])
        return utime + stime
    except Exception:  # noqa: BLE001
        return None


def _read_proc_snapshot(pid: int) -> Dict[str, Optional[float]]:
    clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    jiffies = _read_proc_cpu_jiffies(pid)
    vm_rss_kb = _read_proc_status_value(pid, "VmRSS")
    vm_hwm_kb = _read_proc_status_value(pid, "VmHWM")

    cpu_ms = None if jiffies is None else (jiffies * 1000.0 / float(clk_tck))
    rss_mb = None if vm_rss_kb is None else (vm_rss_kb / 1024.0)
    hwm_mb = None if vm_hwm_kb is None else (vm_hwm_kb / 1024.0)
    return {
        "cpu_ms": cpu_ms,
        "rss_mb": rss_mb,
        "hwm_mb": hwm_mb,
    }


def _read_self_snapshot() -> Dict[str, Optional[float]]:
    return _read_proc_snapshot(os.getpid())


def _collect_audio_files(inputs: List[str], pattern: str) -> List[str]:
    paths: List[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            paths.append(p)
            continue
        if p.is_dir():
            paths.extend(sorted(x.resolve() for x in p.rglob(pattern) if x.is_file()))
            continue
        logger.warning("忽略不存在路径: %s", p)

    # 去重并保持排序
    unique = sorted({str(p): p for p in paths}.values())
    return [str(p) for p in unique]


@dataclass
class RunResult:
    mode: str
    pair_id: str
    audio_path: str
    success: bool
    error: str
    audio_duration_s: float
    asr_ms: float
    slm_ms: float
    e2e_ms: float
    cpu_ms: float
    self_rss_before_mb: float
    self_rss_after_mb: float
    self_hwm_after_mb: float
    slm_reason: str
    slm_applied: bool
    text: str
    text_len: int
    slm_pid_cpu_delta_ms: float
    slm_pid_rss_delta_mb: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "pair_id": self.pair_id,
            "audio_path": self.audio_path,
            "success": self.success,
            "error": self.error,
            "audio_duration_s": round(self.audio_duration_s, 3),
            "asr_ms": round(self.asr_ms, 2),
            "slm_ms": round(self.slm_ms, 2),
            "e2e_ms": round(self.e2e_ms, 2),
            "cpu_ms": round(self.cpu_ms, 2),
            "self_rss_before_mb": round(self.self_rss_before_mb, 2),
            "self_rss_after_mb": round(self.self_rss_after_mb, 2),
            "self_hwm_after_mb": round(self.self_hwm_after_mb, 2),
            "slm_reason": self.slm_reason,
            "slm_applied": self.slm_applied,
            "text": self.text,
            "text_len": self.text_len,
            "slm_pid_cpu_delta_ms": round(self.slm_pid_cpu_delta_ms, 2),
            "slm_pid_rss_delta_mb": round(self.slm_pid_rss_delta_mb, 2),
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对比 ASR-only(F9) 与 ASR+SLM(Shift+F9) 的延迟与资源开销"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="音频文件或目录路径（目录会按 --pattern 递归搜集）",
    )
    parser.add_argument(
        "--pattern",
        default="*.wav",
        help="目录输入时的匹配模式（默认: *.wav）",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="每个音频的重复次数（默认: 3）",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="预热轮次（不计入统计，默认: 1）",
    )

    parser.add_argument("--asr-use-vad", action="store_true", help="启用 ASR 的 VAD")
    parser.add_argument(
        "--asr-no-punc",
        action="store_true",
        help="禁用 ASR 标点模型（默认启用）",
    )
    parser.add_argument("--asr-language", default="zh", help="ASR 语言（默认: zh）")
    parser.add_argument(
        "--asr-hotword",
        default="",
        help="ASR 热词字符串（默认空）",
    )
    parser.add_argument(
        "--asr-batch-size-s",
        type=float,
        default=60.0,
        help="ASR batch_size_s（默认: 60）",
    )

    parser.add_argument(
        "--disable-slm",
        action="store_true",
        help="只跑 ASR-only，不跑 ASR+SLM 对照组",
    )
    parser.add_argument(
        "--slm-endpoint",
        default="http://127.0.0.1:18080/v1/chat/completions",
        help="SLM OpenAI-compatible endpoint",
    )
    parser.add_argument("--slm-model", default="Qwen/Qwen3.5-0.8B", help="SLM model 名称")
    parser.add_argument(
        "--slm-timeout-ms",
        type=int,
        default=12000,
        help="SLM 超时（毫秒）",
    )
    parser.add_argument(
        "--slm-min-chars",
        type=int,
        default=20,
        help="触发 SLM 的最短字符数",
    )
    parser.add_argument(
        "--slm-max-tokens",
        type=int,
        default=96,
        help="SLM 输出 token 上限",
    )
    parser.add_argument(
        "--slm-temperature",
        type=float,
        default=0.0,
        help="SLM temperature",
    )
    parser.add_argument("--slm-top-p", type=float, default=0.9, help="SLM top_p")
    parser.add_argument("--slm-top-k", type=int, default=20, help="SLM top_k")
    parser.add_argument(
        "--slm-enable-thinking",
        action="store_true",
        help="向 endpoint 传 enable_thinking=true",
    )
    parser.add_argument("--slm-api-key", default="", help="SLM API Key（可选）")
    parser.add_argument(
        "--slm-pid",
        type=int,
        default=0,
        help="可选：SLM 服务进程 PID，用于统计该 PID 的 CPU/RSS 增量",
    )

    parser.add_argument(
        "--show-changes",
        type=int,
        default=5,
        help="打印前 N 条 SLM 文本改写样例（默认: 5）",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="可选：输出完整 JSON 报告路径",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别",
    )
    return parser


def _build_asr_options(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "use_vad": bool(args.asr_use_vad),
        "use_punc": not bool(args.asr_no_punc),
        "language": args.asr_language,
        "hotword": args.asr_hotword,
        "batch_size_s": args.asr_batch_size_s,
    }


def _build_slm_config(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "enabled": not bool(args.disable_slm),
        "endpoint": args.slm_endpoint,
        "model": args.slm_model,
        "timeout_ms": args.slm_timeout_ms,
        "min_chars": args.slm_min_chars,
        "max_tokens": args.slm_max_tokens,
        "temperature": args.slm_temperature,
        "top_p": args.slm_top_p,
        "top_k": args.slm_top_k,
        "enable_thinking": bool(args.slm_enable_thinking),
        "api_key": args.slm_api_key,
    }


def _run_once(
    *,
    mode: str,
    pair_id: str,
    audio_path: str,
    asr_server: FunASRServer,
    asr_options: Dict[str, Any],
    polisher: SLMPolisher,
    slm_pid: int,
) -> RunResult:
    self_before = _read_self_snapshot()
    cpu_before = time.process_time()
    e2e_start = time.perf_counter()

    asr_start = time.perf_counter()
    asr_result = asr_server.transcribe_audio(audio_path, options=asr_options)
    asr_ms = (time.perf_counter() - asr_start) * 1000.0

    slm_ms = 0.0
    slm_reason = "not_used"
    slm_applied = False
    text = ""
    error = ""
    audio_duration_s = 0.0
    slm_pid_cpu_delta_ms = 0.0
    slm_pid_rss_delta_mb = 0.0

    if asr_result.get("success"):
        text = str(asr_result.get("text", ""))
        try:
            audio_duration_s = float(asr_result.get("duration", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            audio_duration_s = 0.0

        if mode == "asr_slm":
            slm_before = _read_proc_snapshot(slm_pid) if slm_pid > 0 else None
            polished_text, metrics = polisher.polish(text, long_mode=True)
            slm_after = _read_proc_snapshot(slm_pid) if slm_pid > 0 else None

            text = polished_text
            slm_ms = metrics.latency_ms
            slm_reason = metrics.reason
            slm_applied = bool(metrics.applied)

            if slm_before and slm_after:
                before_cpu = slm_before.get("cpu_ms")
                after_cpu = slm_after.get("cpu_ms")
                if before_cpu is not None and after_cpu is not None:
                    slm_pid_cpu_delta_ms = max(0.0, after_cpu - before_cpu)

                before_rss = slm_before.get("rss_mb")
                after_rss = slm_after.get("rss_mb")
                if before_rss is not None and after_rss is not None:
                    slm_pid_rss_delta_mb = after_rss - before_rss
        else:
            slm_reason = "not_long_mode"
    else:
        error = str(asr_result.get("error", "unknown_error"))

    e2e_ms = (time.perf_counter() - e2e_start) * 1000.0
    cpu_ms = (time.process_time() - cpu_before) * 1000.0
    self_after = _read_self_snapshot()

    return RunResult(
        mode=mode,
        pair_id=pair_id,
        audio_path=audio_path,
        success=bool(asr_result.get("success", False)),
        error=error,
        audio_duration_s=audio_duration_s,
        asr_ms=asr_ms,
        slm_ms=slm_ms,
        e2e_ms=e2e_ms,
        cpu_ms=cpu_ms,
        self_rss_before_mb=float(self_before.get("rss_mb") or 0.0),
        self_rss_after_mb=float(self_after.get("rss_mb") or 0.0),
        self_hwm_after_mb=float(self_after.get("hwm_mb") or 0.0),
        slm_reason=slm_reason,
        slm_applied=slm_applied,
        text=text,
        text_len=len(text.strip()),
        slm_pid_cpu_delta_ms=slm_pid_cpu_delta_ms,
        slm_pid_rss_delta_mb=slm_pid_rss_delta_mb,
    )


def _summarize_mode(records: List[RunResult], mode: str) -> Dict[str, Any]:
    mode_all = [r for r in records if r.mode == mode]
    mode_ok = [r for r in mode_all if r.success]

    e2e_values = [r.e2e_ms for r in mode_ok]
    asr_values = [r.asr_ms for r in mode_ok]
    cpu_values = [r.cpu_ms for r in mode_ok]

    rtf_values = []
    for r in mode_ok:
        if r.audio_duration_s > 0:
            rtf_values.append((r.e2e_ms / 1000.0) / r.audio_duration_s)

    out: Dict[str, Any] = {
        "runs_total": len(mode_all),
        "runs_success": len(mode_ok),
        "runs_fail": len(mode_all) - len(mode_ok),
        "e2e_ms": _format_ms_stats(e2e_values),
        "asr_ms": _format_ms_stats(asr_values),
        "cpu_ms": _format_ms_stats(cpu_values),
        "rtf": {
            "mean": round(statistics.mean(rtf_values), 4) if rtf_values else 0.0,
            "p95": round(_percentile(rtf_values, 95), 4) if rtf_values else 0.0,
        },
        "self_rss_mb": {
            "mean_after": round(statistics.mean([r.self_rss_after_mb for r in mode_ok]), 2)
            if mode_ok
            else 0.0,
            "max_after": round(max([r.self_rss_after_mb for r in mode_ok]), 2)
            if mode_ok
            else 0.0,
            "max_hwm": round(max([r.self_hwm_after_mb for r in mode_ok]), 2)
            if mode_ok
            else 0.0,
        },
    }

    if mode == "asr_slm":
        slm_values = [r.slm_ms for r in mode_ok]
        slm_reason_counter = Counter(r.slm_reason for r in mode_ok)
        out["slm_ms"] = _format_ms_stats(slm_values)
        out["slm_applied_rate"] = round(
            (sum(1 for r in mode_ok if r.slm_applied) / len(mode_ok)) if mode_ok else 0.0,
            4,
        )
        out["slm_reason_counts"] = dict(sorted(slm_reason_counter.items()))

        slm_pid_cpu = [r.slm_pid_cpu_delta_ms for r in mode_ok if r.slm_pid_cpu_delta_ms > 0.0]
        slm_pid_rss = [r.slm_pid_rss_delta_mb for r in mode_ok if r.slm_pid_cpu_delta_ms >= 0.0]
        out["slm_pid_cpu_delta_ms"] = _format_ms_stats(slm_pid_cpu) if slm_pid_cpu else {}
        out["slm_pid_rss_delta_mb"] = _format_ms_stats(slm_pid_rss) if slm_pid_rss else {}

    return out


def _build_pair_overhead(records: List[RunResult]) -> Dict[str, Any]:
    by_pair: Dict[str, Dict[str, RunResult]] = defaultdict(dict)
    for r in records:
        by_pair[r.pair_id][r.mode] = r

    e2e_delta = []
    asr_delta = []
    cpu_delta = []
    slm_values = []

    for pair in by_pair.values():
        base = pair.get("asr_only")
        long = pair.get("asr_slm")
        if not base or not long:
            continue
        if not base.success or not long.success:
            continue

        e2e_delta.append(long.e2e_ms - base.e2e_ms)
        asr_delta.append(long.asr_ms - base.asr_ms)
        cpu_delta.append(long.cpu_ms - base.cpu_ms)
        slm_values.append(long.slm_ms)

    if not e2e_delta:
        return {
            "pairs": 0,
            "e2e_overhead_ms": _format_ms_stats([]),
            "cpu_overhead_ms": _format_ms_stats([]),
            "latency_ratio_mean": 0.0,
        }

    ratios = []
    for pair in by_pair.values():
        base = pair.get("asr_only")
        long = pair.get("asr_slm")
        if not base or not long:
            continue
        if not base.success or not long.success:
            continue
        if base.e2e_ms > 0:
            ratios.append(long.e2e_ms / base.e2e_ms)

    return {
        "pairs": len(e2e_delta),
        "e2e_overhead_ms": _format_ms_stats(e2e_delta),
        "asr_delta_ms": _format_ms_stats(asr_delta),
        "cpu_overhead_ms": _format_ms_stats(cpu_delta),
        "slm_ms": _format_ms_stats(slm_values),
        "latency_ratio_mean": round(statistics.mean(ratios), 4) if ratios else 0.0,
    }


def _print_brief_summary(summary: Dict[str, Any], show_changes: List[Dict[str, Any]]) -> None:
    print("\n=== Benchmark Summary ===")

    base = summary.get("asr_only", {})
    print(
        "[ASR-only] runs={runs_success}/{runs_total} fail={runs_fail} "
        "e2e_mean={e2e_mean}ms p95={e2e_p95}ms cpu_mean={cpu_mean}ms hwm={hwm}MB".format(
            runs_success=base.get("runs_success", 0),
            runs_total=base.get("runs_total", 0),
            runs_fail=base.get("runs_fail", 0),
            e2e_mean=base.get("e2e_ms", {}).get("mean", 0.0),
            e2e_p95=base.get("e2e_ms", {}).get("p95", 0.0),
            cpu_mean=base.get("cpu_ms", {}).get("mean", 0.0),
            hwm=base.get("self_rss_mb", {}).get("max_hwm", 0.0),
        )
    )

    long = summary.get("asr_slm")
    if long:
        print(
            "[ASR+SLM] runs={runs_success}/{runs_total} fail={runs_fail} "
            "e2e_mean={e2e_mean}ms p95={e2e_p95}ms slm_mean={slm_mean}ms "
            "applied_rate={applied_rate:.2%}".format(
                runs_success=long.get("runs_success", 0),
                runs_total=long.get("runs_total", 0),
                runs_fail=long.get("runs_fail", 0),
                e2e_mean=long.get("e2e_ms", {}).get("mean", 0.0),
                e2e_p95=long.get("e2e_ms", {}).get("p95", 0.0),
                slm_mean=long.get("slm_ms", {}).get("mean", 0.0),
                applied_rate=long.get("slm_applied_rate", 0.0),
            )
        )

        reason_counts = long.get("slm_reason_counts", {})
        if reason_counts:
            print("[SLM reasons]", reason_counts)

    pair = summary.get("pair_overhead", {})
    if pair and pair.get("pairs", 0) > 0:
        print(
            "[Overhead] pairs={pairs} e2e+mean={delta_mean}ms p95={delta_p95}ms "
            "ratio_mean={ratio}x".format(
                pairs=pair.get("pairs", 0),
                delta_mean=pair.get("e2e_overhead_ms", {}).get("mean", 0.0),
                delta_p95=pair.get("e2e_overhead_ms", {}).get("p95", 0.0),
                ratio=pair.get("latency_ratio_mean", 0.0),
            )
        )

    if show_changes:
        print("\n=== Sample Changes (ASR -> ASR+SLM) ===")
        for idx, item in enumerate(show_changes, 1):
            print(f"[{idx}] {item['audio_path']}")
            print(f"  ASR : {item['asr_text']}")
            print(f"  SLM : {item['slm_text']}")
            print(f"  reason={item['slm_reason']} slm_ms={item['slm_ms']}\n")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.repeat <= 0:
        logger.error("--repeat 必须大于 0")
        return 2
    if args.warmup < 0:
        logger.error("--warmup 不能小于 0")
        return 2

    audio_files = _collect_audio_files(args.inputs, args.pattern)
    if not audio_files:
        logger.error("未找到可用音频文件")
        return 2

    logger.info("音频文件数: %d", len(audio_files))
    for ap in audio_files:
        logger.debug("  %s", ap)

    asr_options = _build_asr_options(args)
    slm_config = _build_slm_config(args)

    logger.info("初始化 FunASR 服务器...")
    asr_server = FunASRServer()
    init_result = asr_server.initialize()
    if not init_result.get("success", False):
        logger.error("FunASR 初始化失败: %s", init_result)
        return 1

    logger.info("初始化 SLM Polisher（enabled=%s model=%s）", slm_config["enabled"], slm_config["model"])
    polisher = SLMPolisher(slm_config)

    try:
        # 预热（不计入统计）
        if args.warmup > 0:
            warmup_audio = audio_files[0]
            logger.info("开始预热：轮次=%d，样本=%s", args.warmup, warmup_audio)
            for idx in range(args.warmup):
                _ = _run_once(
                    mode="asr_only",
                    pair_id=f"warmup-{idx}",
                    audio_path=warmup_audio,
                    asr_server=asr_server,
                    asr_options=asr_options,
                    polisher=polisher,
                    slm_pid=args.slm_pid,
                )
                if not args.disable_slm:
                    _ = _run_once(
                        mode="asr_slm",
                        pair_id=f"warmup-{idx}",
                        audio_path=warmup_audio,
                        asr_server=asr_server,
                        asr_options=asr_options,
                        polisher=polisher,
                        slm_pid=args.slm_pid,
                    )

        results: List[RunResult] = []
        sample_changes: List[Dict[str, Any]] = []

        logger.info("开始正式测试：repeat=%d", args.repeat)
        for r in range(args.repeat):
            logger.info("第 %d/%d 轮", r + 1, args.repeat)
            for audio_path in audio_files:
                pair_id = f"r{r+1}:{audio_path}"

                base = _run_once(
                    mode="asr_only",
                    pair_id=pair_id,
                    audio_path=audio_path,
                    asr_server=asr_server,
                    asr_options=asr_options,
                    polisher=polisher,
                    slm_pid=args.slm_pid,
                )
                results.append(base)

                if args.disable_slm:
                    continue

                long = _run_once(
                    mode="asr_slm",
                    pair_id=pair_id,
                    audio_path=audio_path,
                    asr_server=asr_server,
                    asr_options=asr_options,
                    polisher=polisher,
                    slm_pid=args.slm_pid,
                )
                results.append(long)

                if (
                    base.success
                    and long.success
                    and base.text.strip()
                    and long.text.strip()
                    and base.text.strip() != long.text.strip()
                    and len(sample_changes) < args.show_changes
                ):
                    sample_changes.append(
                        {
                            "audio_path": audio_path,
                            "asr_text": base.text.strip(),
                            "slm_text": long.text.strip(),
                            "slm_reason": long.slm_reason,
                            "slm_ms": round(long.slm_ms, 2),
                        }
                    )

        summary: Dict[str, Any] = {
            "asr_only": _summarize_mode(results, "asr_only"),
            "pair_overhead": {},
        }

        if not args.disable_slm:
            summary["asr_slm"] = _summarize_mode(results, "asr_slm")
            summary["pair_overhead"] = _build_pair_overhead(results)

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "audio_files": audio_files,
            "repeat": args.repeat,
            "warmup": args.warmup,
            "asr_options": asr_options,
            "slm_config": {
                **slm_config,
                "api_key": "***" if slm_config.get("api_key") else "",
            },
            "slm_pid": args.slm_pid,
            "summary": summary,
            "sample_changes": sample_changes,
            "runs": [r.to_dict() for r in results],
        }

        _print_brief_summary(summary, sample_changes)

        if args.output_json:
            output_path = Path(args.output_json).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n完整 JSON 报告已写入: {output_path}")

        return 0
    finally:
        asr_server.cleanup()


if __name__ == "__main__":
    sys.exit(main())
