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
    "face_detect": False,        # 兼容旧字段
    "algo_mode": "off",          # off | face | object
    "algo_model": "auto",        # auto | yolov8n | fasterrcnn | ssdlite
}

PREVIEW_MODES = {
    "mjpeg_hw": {"label": "MJPEG（优先硬件JPEG）", "player": "mjpeg"},
    "h264_soft": {"label": "H.264 软件（低带宽）", "player": "h264"},
}

# 物体识别可选模型（文件放在 models/）
ALGO_OBJECT_MODELS = {
    "auto": {
        "label": "自动（优先 YOLO）",
        "file": None,
    },
    "yolov8n": {
        "label": "YOLOv8n（推荐）",
        "file": "yolov8n.pt",
    },
    "fasterrcnn": {
        "label": "FasterRCNN",
        "file": "fasterrcnn_mobilenet_v3_large_320_fpn.pth",
    },
    "ssdlite": {
        "label": "SSDLite（轻量备用）",
        "file": "ssdlite320_mobilenet_v3_large_coco.pth",
    },
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
