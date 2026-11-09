from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

import cv2
from flask import Blueprint, Response, current_app, jsonify, request, send_file
from flask_sock import Sock

from backend.app.config import PREVIEW_MODES, RECORD_DIR, SNAP_DIR
from backend.app.services import media_index, metrics_probe
from backend.app.services.settings_store import load_settings, save_settings
from backend.app.stream.effects import draw_osd
from backend.app.stream.sinks.preview_mjpeg import mjpeg_generator

LOG = logging.getLogger("webcam.api")
api_bp = Blueprint("api", __name__)
sock = Sock()


def _rt():
    return current_app.config["RUNTIME"]


def apply_settings_to_runtime(data: dict, runtime=None) -> None:
    rt = runtime or _rt()
    rt.source.set_effects(
        rotate=data.get("rotate"),
        de_red=data.get("de_red"),
        fps=data.get("preview_fps"),
    )
    rt.recorder.set_osd(bool(data.get("osd", True)))
    if data.get("preview_fps") is not None:
        fps = float(data["preview_fps"])
        rt.recorder.set_fps(fps)
        rt.preview_h264.set_fps(fps)
    if "auto_record" in data:
        was_auto = rt.recorder.info().get("auto")
        rt.recorder.set_auto(bool(data["auto_record"]))
        # 打开自动录制时尝试立刻开始
        if data["auto_record"] and not was_auto:
            try:
                rt.recorder.ensure_auto_recording()
            except Exception as e:
                LOG.warning("自动录制启动失败: %s", e)


@api_bp.get("/health")
def health():
    rt = _rt()
    settings = load_settings()
    ready = rt.hub.has_frame()
    rec = rt.recorder.info()
    effects = rt.source.get_effects()
    return jsonify(
        {
            "ok": ready,
            "camera": "ready" if ready else "starting",
            "preview_mode": settings.get("preview_mode"),
            "preview_fps": effects.get("fps"),
            "recording": rec["recording"],
            "auto_record": rec.get("auto"),
            "de_red": effects["de_red"],
            "osd": settings.get("osd"),
            "retention_days": settings.get("retention_days"),
            "mjpeg": rt.preview_mjpeg.stats(),
            "h264": rt.preview_h264.stats(),
        }
    ), (200 if ready else 503)


@api_bp.get("/settings")
def get_settings():
    data = load_settings()
    data["preview_modes"] = PREVIEW_MODES
    data["storage"] = media_index.storage_estimate(
        int(data.get("retention_days", 7)),
        bool(data.get("auto_record", False)),
    )
    return jsonify(data)


@api_bp.post("/settings")
def post_settings():
    patch = request.get_json(silent=True) or {}
    saved = save_settings(patch)
    apply_settings_to_runtime(saved)
    # 滚存天数变更时立刻清理一次
    if "retention_days" in patch:
        media_index.cleanup_expired(int(saved["retention_days"]))
    return jsonify(
        {
            "ok": True,
            **saved,
            "preview_modes": PREVIEW_MODES,
            "storage": media_index.storage_estimate(
                int(saved.get("retention_days", 7)),
                bool(saved.get("auto_record", False)),
            ),
        }
    )


@api_bp.get("/preview/mjpeg")
def preview_mjpeg():
    rt = _rt()
    return Response(
        mjpeg_generator(rt.preview_mjpeg),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@api_bp.post("/snapshot")
@api_bp.get("/snapshot")
def snapshot():
    rt = _rt()
    frame, _ = rt.hub.get_latest()
    if frame is None:
        return jsonify({"error": "还没有画面"}), 503
    settings = load_settings()
    out = frame.copy()
    if settings.get("osd", True):
        out = draw_osd(out)
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return jsonify({"error": "编码失败"}), 500
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = SNAP_DIR / name
    path.write_bytes(buf.tobytes())
    resp = send_file(path, mimetype="image/jpeg", as_attachment=True, download_name=name)
    resp.headers["X-Filename"] = name
    return resp


@api_bp.post("/record/start")
def record_start():
    rt = _rt()
    try:
        path = rt.recorder.start_recording()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "file": path.name})


@api_bp.post("/record/stop")
def record_stop():
    rt = _rt()
    path = rt.recorder.stop_recording()
    if path is None:
        return jsonify({"error": "当前没有在录制"}), 400
    return jsonify({"ok": True, "file": path.name})


@api_bp.get("/record/status")
def record_status():
    return jsonify(_rt().recorder.info())


@api_bp.get("/calendar")
def calendar_month():
    now = datetime.now()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    if month < 1 or month > 12 or year < 2000 or year > 2100:
        return jsonify({"error": "年月无效"}), 400
    return jsonify(media_index.calendar_month(year, month))


@api_bp.get("/calendar/day")
def calendar_day():
    date_str = request.args.get("date", "")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "日期格式应为 YYYY-MM-DD"}), 400
    active = _rt().recorder.info().get("file")
    return jsonify(media_index.day_items(date_str, active_recording=active))


@api_bp.get("/storage")
def storage_info():
    settings = load_settings()
    return jsonify(
        media_index.storage_estimate(
            int(settings.get("retention_days", 7)),
            bool(settings.get("auto_record", False)),
        )
    )


@api_bp.post("/storage/cleanup")
def storage_cleanup():
    settings = load_settings()
    result = media_index.cleanup_expired(int(settings.get("retention_days", 7)))
    return jsonify({"ok": True, **result})


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=10,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def _ensure_mp4(src: Path) -> tuple[Path, bool]:
    if src.suffix.lower() == ".mp4" and src.exists() and src.stat().st_size > 0:
        return src, False
    mp4 = src.with_suffix(".mp4")
    if mp4.exists() and mp4.stat().st_size > 0:
        return mp4, False
    if not src.exists():
        raise FileNotFoundError(src.name)
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-an", "-movflags", "+faststart", str(mp4),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mp4, True


@api_bp.get("/files")
def list_files():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)

    def collect(folder: Path, kind: str, prefix: str, patterns: list[str]):
        files: list[Path] = []
        for pat in patterns:
            files.extend(folder.glob(pat))
        items = []
        for p in sorted(set(files), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".avi" and p.with_suffix(".mp4").exists():
                continue
            items.append({"name": p.name, "kind": kind, "url": f"{prefix}/{p.name}"})
        return items[:20]

    return jsonify(
        {
            "snapshots": collect(SNAP_DIR, "截图", "/files/snapshots", ["*.jpg"]),
            "recordings": collect(RECORD_DIR, "录像", "/files/recordings", ["*.mp4", "*.avi"]),
        }
    )


@api_bp.get("/playback/list")
def playback_list():
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    seen: set[str] = set()
    files = list(RECORD_DIR.glob("record_*.mp4")) + list(RECORD_DIR.glob("record_*.avi"))
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        if path.stem in seen:
            continue
        mp4 = path if path.suffix.lower() == ".mp4" else path.with_suffix(".mp4")
        avi = path if path.suffix.lower() == ".avi" else path.with_suffix(".avi")
        main = mp4 if mp4.exists() else avi
        if not main.exists():
            continue
        seen.add(path.stem)
        start_text = path.stem.replace("record_", "")
        try:
            dt = datetime.strptime(start_text, "%Y%m%d_%H%M%S")
            start_text = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        duration = _probe_duration(main)
        items.append(
            {
                "id": path.stem,
                "title": main.name,
                "start_text": start_text,
                "duration": duration,
                "duration_text": f"{int(duration // 60):02d}:{int(duration % 60):02d}",
                "size_mb": round(main.stat().st_size / 1024 / 1024, 2),
                "playable": main.suffix.lower() == ".mp4",
            }
        )
    return jsonify({"items": items})


@api_bp.post("/playback/open/<rec_id>")
def playback_open(rec_id: str):
    if "/" in rec_id or ".." in rec_id:
        return jsonify({"error": "非法编号"}), 400
    mp4 = RECORD_DIR / f"{rec_id}.mp4"
    avi = RECORD_DIR / f"{rec_id}.avi"
    src = mp4 if mp4.exists() else avi
    if not src.exists():
        return jsonify({"error": "录像不存在"}), 404
    try:
        playable, converted = _ensure_mp4(src)
    except Exception as e:
        return jsonify({"error": f"转码失败: {e}"}), 500
    return jsonify(
        {
            "ok": True,
            "title": playable.name,
            "url": f"/files/recordings/{playable.name}",
            "converted": converted,
            "duration": _probe_duration(playable),
        }
    )


@api_bp.post("/metrics/preview-bench")
def preview_bench():
    body = request.get_json(silent=True) or {}
    seconds = float(body.get("seconds", 10))
    seconds = max(3.0, min(30.0, seconds))
    settings = load_settings()
    mode = settings.get("preview_mode")
    result = metrics_probe.sample_process_cpu(seconds=seconds)
    rt = _rt()
    result.update(
        {
            "preview_mode": mode,
            "mjpeg": rt.preview_mjpeg.stats(),
            "h264": rt.preview_h264.stats(),
        }
    )
    return jsonify({"ok": True, **result})


def register_sock_routes(sock_ext: Sock) -> None:
    @sock_ext.route("/api/preview/h264")
    def preview_h264(ws):
        rt = current_app.config["RUNTIME"]

        def send_bytes(data: bytes) -> None:
            ws.send(data)

        rt.preview_h264.add_client(send_bytes)
        try:
            while True:
                msg = ws.receive()
                if msg is None:
                    break
        finally:
            rt.preview_h264.remove_client(send_bytes)
