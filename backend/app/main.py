from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from flask import Flask, send_from_directory
from flask_sock import Sock

# 保证以仓库根目录为 import 起点
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api.routes import api_bp, apply_settings_to_runtime, register_sock_routes
from backend.app.config import FRONTEND_DIR, HOST, PORT, RECORD_DIR, SNAP_DIR
from backend.app.runtime import create_runtime
from backend.app.services import media_index
from backend.app.services.settings_store import load_settings

LOG = logging.getLogger("webcam")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    sock = Sock(app)
    runtime = create_runtime()
    app.config["RUNTIME"] = runtime

    app.register_blueprint(api_bp, url_prefix="/api")
    register_sock_routes(sock)

    @app.get("/")
    def index_page():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/playback")
    def playback_page():
        return send_from_directory(FRONTEND_DIR, "playback.html")

    @app.get("/calendar")
    def calendar_page():
        return send_from_directory(FRONTEND_DIR, "calendar.html")

    @app.get("/assets/<path:filename>")
    def frontend_assets(filename: str):
        return send_from_directory(FRONTEND_DIR / "assets", filename)

    @app.get("/files/snapshots/<path:name>")
    def file_snapshot(name: str):
        return send_from_directory(SNAP_DIR, name, as_attachment=True)

    @app.get("/files/recordings/<path:name>")
    def file_recording(name: str):
        lower = name.lower()
        # 正式 mp4 / 录制中的分片 part.mp4 都给网页播放，不要强制下载
        as_attachment = not (lower.endswith(".mp4"))
        return send_from_directory(RECORD_DIR, name, as_attachment=as_attachment)

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)

    app = create_app()
    rt = app.config["RUNTIME"]
    settings = load_settings()
    # 预览叠加算法结果（人脸框），录像仍用干净帧
    rt.preview_mjpeg.set_overlay(rt.algorithm.annotate)
    rt.preview_h264.set_overlay(rt.algorithm.annotate)
    apply_settings_to_runtime(settings, runtime=rt)

    # 预览/录像先订阅；算法异步推理，避免 YOLO 堵死 Hub 导致预览黑屏
    rt.preview_mjpeg.start()
    rt.preview_h264.start()
    rt.recorder.start()
    rt.algorithm.start()
    rt.source.start()

    for _ in range(50):
        if rt.hub.has_frame():
            break
        time.sleep(0.1)
    if not rt.hub.has_frame():
        LOG.warning("启动时还没拿到画面，请稍后刷新网页")
    else:
        LOG.info("首帧已就绪，开始提供网页服务")

    # 启动时：封装残留分片、清理过期文件 + 后台滚存
    try:
        fixed = media_index.finalize_orphan_parts()
        if fixed:
            LOG.info("启动封装残留录像 %s 个", len(fixed))
    except Exception as e:
        LOG.warning("启动封装残留录像失败: %s", e)
    try:
        cleaned = media_index.cleanup_expired(int(settings.get("retention_days", 7)))
        if cleaned["removed"]:
            LOG.info("启动滚存清理删除 %s 个文件", len(cleaned["removed"]))
    except Exception as e:
        LOG.warning("启动滚存清理失败: %s", e)

    threading.Thread(
        target=media_index.retention_loop,
        args=(lambda: load_settings().get("retention_days", 7), 600.0),
        name="retention-loop",
        daemon=True,
    ).start()

    # 自动录制：等有画面后再开
    if settings.get("auto_record"):
        for _ in range(30):
            if rt.hub.has_frame():
                try:
                    rt.recorder.ensure_auto_recording()
                    LOG.info("已按设置开启自动录制")
                except Exception as e:
                    LOG.warning("自动录制开启失败: %s", e)
                break
            time.sleep(0.1)

    try:
        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
    finally:
        rt.algorithm.stop()
        rt.recorder.stop()
        rt.preview_h264.stop()
        rt.preview_mjpeg.stop()
        rt.source.stop()


if __name__ == "__main__":
    main()
