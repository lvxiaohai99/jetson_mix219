from __future__ import annotations

import calendar
import logging
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.config import ESTIMATE_MB_PER_MIN, RECORD_DIR, SNAP_DIR

LOG = logging.getLogger("webcam.media")

_NAME_RE = re.compile(r"^(?:snapshot|record)_(\d{8})_(\d{6})")


def _parse_file_dt(path: Path) -> datetime | None:
    # record_xxx.part.mp4 → stem 先去掉 .part
    stem = path.stem
    if stem.endswith(".part"):
        stem = stem[: -len(".part")]
    m = _NAME_RE.match(stem)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return None


def is_playable_mp4(path: Path) -> bool:
    """快速判断 MP4 是否带有可解析头（录制中的旧格式会失败）。"""
    try:
        if not path.is_file() or path.stat().st_size < 64:
            return False
        # 分片 MP4（.part）本身可读
        if path.name.endswith(".part.mp4"):
            return True
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        ).strip()
        return bool(out) and out.lower() != "n/a"
    except Exception:
        return False


def _iter_media_files() -> list[tuple[Path, str, datetime]]:
    items: list[tuple[Path, str, datetime]] = []
    for folder, kind, patterns in (
        (SNAP_DIR, "snapshot", ["*.jpg"]),
        (RECORD_DIR, "recording", ["*.mp4", "*.avi"]),
    ):
        if not folder.exists():
            continue
        for pat in patterns:
            for p in folder.glob(pat):
                if not p.is_file() or p.name.startswith("."):
                    continue
                # 有正式 mp4 时忽略同名 avi；有正式 mp4 时忽略仍残留的 .part
                if p.suffix.lower() == ".avi" and p.with_suffix(".mp4").exists():
                    continue
                if p.name.endswith(".part.mp4"):
                    final = Path(str(p)[: -len(".part.mp4")] + ".mp4")
                    if final.exists() and final.stat().st_size > 0:
                        continue
                dt = _parse_file_dt(p)
                if dt is None:
                    continue
                items.append((p, kind, dt))
    return items


def calendar_month(year: int, month: int) -> dict:
    days_in_month = calendar.monthrange(year, month)[1]
    counts = {d: {"snapshots": 0, "recordings": 0, "bytes": 0} for d in range(1, days_in_month + 1)}
    for path, kind, dt in _iter_media_files():
        if dt.year != year or dt.month != month:
            continue
        slot = counts.get(dt.day)
        if not slot:
            continue
        if kind == "snapshot":
            slot["snapshots"] += 1
        else:
            slot["recordings"] += 1
        try:
            slot["bytes"] += path.stat().st_size
        except Exception:
            pass

    day_list = []
    for d in range(1, days_in_month + 1):
        c = counts[d]
        total = c["snapshots"] + c["recordings"]
        day_list.append(
            {
                "date": f"{year:04d}-{month:02d}-{d:02d}",
                "day": d,
                "has_media": total > 0,
                "snapshots": c["snapshots"],
                "recordings": c["recordings"],
                "bytes": c["bytes"],
            }
        )

    first_weekday = calendar.monthrange(year, month)[0]  # Mon=0
    return {
        "year": year,
        "month": month,
        "first_weekday": first_weekday,
        "days": day_list,
    }


def day_items(date_str: str, active_recording: str | None = None) -> dict:
    day = datetime.strptime(date_str, "%Y-%m-%d")
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    items = []
    for path, kind, dt in _iter_media_files():
        if not (start <= dt < end):
            continue
        prefix = "/files/snapshots" if kind == "snapshot" else "/files/recordings"
        in_progress = False
        playable = True
        display_name = path.name
        url_name = path.name
        if kind == "recording":
            stem = path.name
            if stem.endswith(".part.mp4"):
                in_progress = True
                display_name = stem.replace(".part.mp4", ".mp4")
                playable = True  # 分片文件可预览
            elif active_recording and path.name == active_recording:
                # 兼容旧写入器：正在写的标准 mp4 通常还没有 moov
                part = path.with_suffix(".part.mp4")
                if part.exists():
                    url_name = part.name
                    in_progress = True
                    playable = True
                else:
                    in_progress = True
                    playable = is_playable_mp4(path)
            else:
                playable = is_playable_mp4(path)
        items.append(
            {
                "name": display_name,
                "file": url_name,
                "kind": "截图" if kind == "snapshot" else ("录像中" if in_progress else "录像"),
                "kind_id": kind,
                "time": dt.strftime("%H:%M:%S"),
                "url": f"{prefix}/{url_name}",
                "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
                "playable": playable,
                "recording": in_progress,
            }
        )
    items.sort(key=lambda x: x["time"])
    return {"date": date_str, "items": items}


def current_usage_bytes() -> int:
    total = 0
    for path, _kind, _dt in _iter_media_files():
        try:
            total += path.stat().st_size
        except Exception:
            pass
    return total


def storage_estimate(retention_days: int, auto_record: bool) -> dict:
    used = current_usage_bytes()
    # 按全天自动录制估算滚存上限
    mb_per_day = ESTIMATE_MB_PER_MIN * 60 * 24 if auto_record else ESTIMATE_MB_PER_MIN * 60 * 8
    est_mb = mb_per_day * max(1, retention_days)
    return {
        "retention_days": retention_days,
        "auto_record": auto_record,
        "estimate_mb_per_min": ESTIMATE_MB_PER_MIN,
        "estimate_mb_per_day": round(mb_per_day, 1),
        "estimate_total_mb": round(est_mb, 1),
        "estimate_total_gb": round(est_mb / 1024, 2),
        "used_mb": round(used / 1024 / 1024, 2),
        "used_gb": round(used / 1024 / 1024 / 1024, 3),
        "note": "预计按 720p H.264 全天录制约 8MB/分钟估算；关闭自动录制时按每天约 8 小时估算",
    }


def cleanup_expired(retention_days: int) -> dict:
    cutoff = datetime.now() - timedelta(days=max(1, retention_days))
    removed = []
    kept = 0
    for path, kind, dt in _iter_media_files():
        if dt >= cutoff:
            kept += 1
            continue
        try:
            path.unlink(missing_ok=True)
            removed.append({"name": path.name, "kind": kind, "date": dt.isoformat(sep=" ", timespec="seconds")})
            # 同步删掉同名 avi/mp4 残留
            if kind == "recording":
                twin = path.with_suffix(".avi" if path.suffix.lower() == ".mp4" else ".mp4")
                if twin.exists():
                    twin.unlink(missing_ok=True)
        except Exception as e:
            LOG.warning("删除过期文件失败 %s: %s", path, e)
    return {"cutoff": cutoff.isoformat(sep=" ", timespec="seconds"), "removed": removed, "kept": kept}


def retention_loop(get_days_callable, interval_sec: float = 600.0) -> None:
    """后台滚存清理。get_days_callable() -> int"""
    while True:
        try:
            days = int(get_days_callable())
            result = cleanup_expired(days)
            if result["removed"]:
                LOG.info("滚存清理删除 %s 个文件", len(result["removed"]))
        except Exception as e:
            LOG.warning("滚存清理异常: %s", e)
        time.sleep(interval_sec)
