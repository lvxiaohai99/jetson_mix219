from __future__ import annotations

import os
import threading
import time
from typing import Any


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            parts = f.read().split()
        # utime + stime
        return int(parts[13]) + int(parts[14])
    except Exception:
        return None


def sample_process_cpu(seconds: float = 10.0, interval: float = 0.5) -> dict[str, Any]:
    """采样当前进程 CPU 占用百分比（相对单核，可超过 100）。"""
    pid = os.getpid()
    clk = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    samples: list[float] = []
    prev = _read_proc_cpu_jiffies(pid)
    prev_t = time.time()
    end = time.time() + max(1.0, seconds)
    while time.time() < end:
        time.sleep(interval)
        now = _read_proc_cpu_jiffies(pid)
        now_t = time.time()
        if prev is None or now is None:
            prev, prev_t = now, now_t
            continue
        dt = max(now_t - prev_t, 1e-6)
        dj = max(now - prev, 0)
        cpu = (dj / clk) / dt * 100.0
        samples.append(cpu)
        prev, prev_t = now, now_t
    avg = sum(samples) / len(samples) if samples else 0.0
    return {
        "seconds": seconds,
        "samples": len(samples),
        "cpu_avg": round(avg, 1),
        "cpu_max": round(max(samples), 1) if samples else 0.0,
        "cpu_min": round(min(samples), 1) if samples else 0.0,
    }


def run_bench_async(seconds: float, callback) -> None:
    def _job():
        result = sample_process_cpu(seconds=seconds)
        callback(result)

    threading.Thread(target=_job, name="preview-bench", daemon=True).start()
