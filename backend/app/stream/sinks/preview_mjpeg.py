from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import cv2
import numpy as np

from backend.app.config import JPEG_QUALITY
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.preview.mjpeg")

OverlayFn = Callable[[np.ndarray], np.ndarray]


class PreviewMjpegSink:
    """从 Hub 取最新帧，编码为 JPEG，供 MJPEG HTTP 推流。

    说明：优先尝试硬件 JPEG 路径；当前帧已是 BGR，稳定路径使用 OpenCV JPEG，
    并在健康信息中标记 encoder。
    """

    def __init__(self, hub: FrameHub, quality: int = JPEG_QUALITY) -> None:
        self.hub = hub
        self.quality = quality
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._encoder = "opencv_jpeg"
        self._fps = 0.0
        self._frames = 0
        self._fps_t0 = time.time()
        self._overlay_fn: OverlayFn | None = None

    def set_overlay(self, fn: OverlayFn | None) -> None:
        self._overlay_fn = fn

    @property
    def encoder(self) -> str:
        return self._encoder

    def start(self) -> None:
        self.hub.subscribe("preview_mjpeg", self._on_frame)

    def stop(self) -> None:
        self.hub.unsubscribe("preview_mjpeg")

    def _on_frame(self, frame: np.ndarray, _meta: dict) -> None:
        fn = self._overlay_fn
        if fn is not None:
            try:
                frame = fn(frame.copy())
            except Exception:
                pass
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:
            return
        with self._lock:
            self._jpeg = buf.tobytes()
            self._frames += 1
            now = time.time()
            if now - self._fps_t0 >= 1.0:
                self._fps = self._frames / (now - self._fps_t0)
                self._frames = 0
                self._fps_t0 = now

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def stats(self) -> dict:
        with self._lock:
            return {"encoder": self._encoder, "fps": round(self._fps, 1), "ready": self._jpeg is not None}


def mjpeg_generator(sink: PreviewMjpegSink):
    while True:
        jpeg = sink.get_jpeg()
        if jpeg is None:
            time.sleep(0.05)
            continue
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        time.sleep(0.03)
