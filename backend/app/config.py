from __future__ import annotations

from pathlib import Path

HOST = "0.0.0.0"
PORT = 8080

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
RECORD_DIR = ROOT_DIR / "recordings"
SNAP_DIR = ROOT_DIR / "snapshots"
SETTINGS_PATH = ROOT_DIR / "settings.json"

JPEG_QUALITY = 70
H264_CRF = 23
# 全天连续录制约占用（按当前 720p H.264 实测量级）
ESTIMATE_MB_PER_MIN = 8.0

DEFAULT_SETTINGS = {
    "rotate": 0,
    "scale": 1.0,
    "de_red": 55,
    "osd": True,
    "preview_mode": "mjpeg_hw",  # mjpeg_hw | h264_soft
    "preview_fps": 15,           # 5~30
    "auto_record": False,        # 启动后自动录制
    "retention_days": 7,         # 滚存天数
}

PREVIEW_MODES = {
    "mjpeg_hw": {"label": "MJPEG（优先硬件JPEG）", "player": "mjpeg"},
    "h264_soft": {"label": "H.264 软件（低带宽）", "player": "h264"},
}


def build_gst_pipeline(fps: int | float) -> str:
    fps_i = max(5, min(30, int(round(float(fps)))))
    return (
        "nvarguscamerasrc sensor-id=0 ! "
        f"video/x-raw(memory:NVMM),width=1280,height=720,framerate={fps_i}/1 ! "
        "nvvidconv ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=1"
    )
