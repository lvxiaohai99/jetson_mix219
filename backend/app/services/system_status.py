from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
RESTART_NVARGUS = ROOT / "scripts" / "restart_nvargus.sh"


def _read_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                key = parts[0].rstrip(":")
                try:
                    out[key] = int(parts[1])
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _read_cpu_times() -> tuple[int, int] | None:
    """返回 (total, idle_all) jiffies。"""
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            parts = f.readline().split()
        # user nice system idle iowait irq softirq steal
        nums = [int(x) for x in parts[1:9]]
        idle = nums[3] + nums[4]
        total = sum(nums)
        return total, idle
    except Exception:
        return None


def _read_proc_name(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip() or str(pid)
    except Exception:
        return str(pid)


def _read_proc_rss_kb(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/statm", encoding="utf-8") as f:
            # size resident shared ...
            parts = f.read().split()
        page = os.sysconf("SC_PAGE_SIZE")
        return int(parts[1]) * page // 1024
    except Exception:
        return 0


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            parts = f.read().split()
        return int(parts[13]) + int(parts[14])
    except Exception:
        return None


def _sample_cpu_and_top(limit: int = 10, sample_s: float = 0.2) -> tuple[float, list[dict[str, Any]]]:
    """一次短采样同时得到整机 CPU% 与 Top 进程。"""
    sys_a = _read_cpu_times()
    pids: list[int] = []
    try:
        for name in os.listdir("/proc"):
            if name.isdigit():
                pids.append(int(name))
    except OSError:
        pids = []

    first: dict[int, int] = {}
    for pid in pids:
        j = _read_proc_cpu_jiffies(pid)
        if j is not None:
            first[pid] = j

    t0 = time.time()
    time.sleep(max(0.05, sample_s))
    dt = max(time.time() - t0, 1e-3)
    clk = os.sysconf("SC_CLK_TCK")

    sys_b = _read_cpu_times()
    cpu_pct = 0.0
    if sys_a and sys_b:
        d_total = max(sys_b[0] - sys_a[0], 1)
        d_idle = max(sys_b[1] - sys_a[1], 0)
        cpu_pct = round(max(0.0, (1.0 - d_idle / d_total) * 100.0), 1)

    rows: list[dict[str, Any]] = []
    for pid, j0 in first.items():
        j1 = _read_proc_cpu_jiffies(pid)
        if j1 is None:
            continue
        cpu = max(0.0, (j1 - j0) / clk / dt * 100.0)
        rss_kb = _read_proc_rss_kb(pid)
        if cpu < 0.05 and rss_kb < 20 * 1024:
            continue
        rows.append(
            {
                "pid": pid,
                "name": _read_proc_name(pid),
                "cpu": round(cpu, 1),
                "rss_mb": round(rss_kb / 1024.0, 1),
            }
        )
    rows.sort(key=lambda r: (-r["cpu"], -r["rss_mb"]))
    return cpu_pct, rows[: max(1, min(20, limit))]


def _argus_active() -> bool | None:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "nvargus-daemon"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return None


def can_restart_nvargus_nopass() -> bool:
    """检测是否已配置免密重启 Argus（网页恢复按钮前置条件）。

    只看 sudoers 落盘，避免每次轮询都触发 sudo 鉴权刷日志。
    """
    return RESTART_NVARGUS.is_file() and Path("/etc/sudoers.d/v2l4-nvargus").is_file()


def restart_nvargus_daemon() -> dict[str, Any]:
    """免密重启 nvargus-daemon。失败时返回 need_setup。"""
    script = RESTART_NVARGUS
    if not script.is_file():
        return {
            "ok": False,
            "need_setup": True,
            "error": f"缺少脚本 {script}",
            "setup_cmd": f"bash {ROOT}/scripts/install_nvargus_sudoers.sh",
        }
    try:
        r = subprocess.run(
            ["sudo", "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "重启 nvargus 超时"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"exit={r.returncode}"
        need = "password" in err.lower() or "密码" in err or r.returncode == 1
        return {
            "ok": False,
            "need_setup": need,
            "error": err,
            "setup_cmd": f"bash {ROOT}/scripts/install_nvargus_sudoers.sh",
        }
    # 等 daemon 起来
    for _ in range(20):
        if _argus_active():
            break
        time.sleep(0.2)
    return {
        "ok": True,
        "nvargus_active": _argus_active(),
        "cma": _cma_snapshot(),
    }


def _cma_snapshot() -> dict[str, float | bool]:
    mem = _read_meminfo()
    total = mem.get("CmaTotal", 0)
    free = mem.get("CmaFree", 0)
    used_pct = round((total - free) * 100.0 / total, 1) if total else 0.0
    return {
        "total_mb": round(total / 1024.0, 2),
        "free_mb": round(free / 1024.0, 2),
        "used_percent": used_pct,
        "low": bool(total and free < 8 * 1024),
    }


def collect_system_status(*, top_n: int = 10) -> dict[str, Any]:
    mem = _read_meminfo()
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", 0)
    free_kb = mem.get("MemFree", 0)
    cma_total = mem.get("CmaTotal", 0)
    cma_free = mem.get("CmaFree", 0)
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)

    used_kb = max(total_kb - avail_kb, 0)
    mem_pct = round(used_kb * 100.0 / total_kb, 1) if total_kb else 0.0
    cma_pct_used = (
        round((cma_total - cma_free) * 100.0 / cma_total, 1) if cma_total else 0.0
    )
    cma_warn = bool(cma_total and cma_free < 8 * 1024)  # <8MB

    load1 = load5 = load15 = 0.0
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            parts = f.read().split()
        load1, load5, load15 = float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        pass

    cpu_pct, tops = _sample_cpu_and_top(limit=top_n, sample_s=0.2)

    hints: list[str] = []
    recover_ready = can_restart_nvargus_nopass()
    # 取流中 CMA 被占是正常的；仅在「没在正常出帧」时当作风险提示
    if cma_warn:
        if recover_ready:
            hints.append(
                "CMA 剩余很少。若画面正常可忽略；若黑屏请点「恢复摄像头」"
                "（必要时会自动重启预览服务）"
            )
        else:
            hints.append(
                "CMA 偏低且未配置免密恢复。请执行："
                f"bash {ROOT}/scripts/install_nvargus_sudoers.sh"
            )
    if mem_pct >= 90:
        hints.append("系统内存紧张，建议关闭占用大的进程")
    if cpu_pct >= 90:
        hints.append("CPU 占用很高")

    return {
        "cpu_percent": cpu_pct,
        "loadavg": [load1, load5, load15],
        "memory": {
            "total_mb": round(total_kb / 1024.0, 1),
            "used_mb": round(used_kb / 1024.0, 1),
            "available_mb": round(avail_kb / 1024.0, 1),
            "free_mb": round(free_kb / 1024.0, 1),
            "percent": mem_pct,
            "swap_used_mb": round((swap_total - swap_free) / 1024.0, 1) if swap_total else 0.0,
            "swap_total_mb": round(swap_total / 1024.0, 1) if swap_total else 0.0,
        },
        "cma": {
            "total_mb": round(cma_total / 1024.0, 2),
            "free_mb": round(cma_free / 1024.0, 2),
            "used_percent": cma_pct_used,
            "low": cma_warn,
            "note": "Jetson CSI/Argus 专用连续物理内存，与普通可用内存不是一回事",
        },
        "nvargus_active": _argus_active(),
        "recover": {
            "ready": recover_ready,
            "setup_cmd": f"bash {ROOT}/scripts/install_nvargus_sudoers.sh",
        },
        "top_processes": tops,
        "hints": hints,
        "ts": time.time(),
    }
