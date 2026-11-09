from __future__ import annotations

import json
import logging
from typing import Any

from backend.app.config import ALGO_OBJECT_MODELS, DEFAULT_SETTINGS, PREVIEW_MODES, SETTINGS_PATH

LOG = logging.getLogger("webcam.settings")


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    out.update(data or {})
    out["rotate"] = int(out.get("rotate", 0)) % 360
    out["scale"] = float(out.get("scale", 1.0))
    out["de_red"] = int(max(0, min(100, int(out.get("de_red", 55)))))
    out["osd"] = bool(out.get("osd", True))
    mode = str(out.get("preview_mode", "mjpeg_hw"))
    if mode not in PREVIEW_MODES:
        mode = "mjpeg_hw"
    out["preview_mode"] = mode
    out["preview_fps"] = int(max(5, min(30, int(out.get("preview_fps", 15)))))
    out["auto_record"] = bool(out.get("auto_record", False))
    out["retention_days"] = int(max(1, min(365, int(out.get("retention_days", 7)))))
    # 算法模式：优先 algo_mode；兼容旧 face_detect
    mode = str(out.get("algo_mode") or "").lower()
    if mode not in ("off", "face", "object"):
        mode = "face" if bool(out.get("face_detect", False)) else "off"
    out["algo_mode"] = mode
    out["face_detect"] = mode == "face"
    model = str(out.get("algo_model") or "auto").lower()
    if model not in ALGO_OBJECT_MODELS:
        model = "auto"
    out["algo_model"] = model
    return out


def load_settings() -> dict[str, Any]:
    data: dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception as e:
            LOG.warning("读取设置失败: %s", e)
    return _normalize(data)


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    data = load_settings()
    for key in (
        "rotate",
        "scale",
        "de_red",
        "osd",
        "preview_mode",
        "preview_fps",
        "auto_record",
        "retention_days",
        "face_detect",
        "algo_mode",
        "algo_model",
    ):
        if key in patch:
            data[key] = patch[key]
    # 若只改了 face_detect，同步 algo_mode
    if "algo_mode" not in patch and "face_detect" in patch:
        data["algo_mode"] = "face" if patch.get("face_detect") else "off"
    data = _normalize(data)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
