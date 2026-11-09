from __future__ import annotations

import logging
import threading
import time

import cv2

from backend.app.config import build_gst_pipeline
from backend.app.stream.effects import build_edge_weight, correct_red_edge, rotate_frame
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.source")


class CameraSource:
    """唯一摄像头源：取流 → 特效 → 发布到 Hub。"""

    def __init__(self, hub: FrameHub, fps: float = 15.0) -> None:
        self.hub = hub
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._rotate = 0
        self._de_red = 0
        self._fps = float(fps)
        self._reopen = False
        self._weight = None
        self._weight_shape = None
        # 预览/录像/算法都关掉时暂停取流，进一步省 CPU
        self._capture_wanted = True
        self._force_until = 0.0

    def set_effects(
        self,
        *,
        rotate: int | None = None,
        de_red: int | None = None,
        fps: float | None = None,
    ) -> None:
        with self._lock:
            if rotate is not None:
                self._rotate = int(rotate) % 360
            if de_red is not None:
                self._de_red = int(max(0, min(100, de_red)))
            if fps is not None:
                new_fps = float(max(5, min(30, fps)))
                if abs(new_fps - self._fps) >= 0.1:
                    self._fps = new_fps
                    self._reopen = True

    def set_capture_wanted(self, wanted: bool) -> None:
        with self._lock:
            wanted = bool(wanted)
            if wanted == self._capture_wanted:
                return
            self._capture_wanted = wanted
            if wanted:
                self._reopen = True  # 尽快重新打开
            LOG.info("摄像头采集: %s", "需要" if wanted else "暂停")

    def request_brief_capture(self, seconds: float = 3.0) -> None:
        """截图等短时需求：临时拉起采集。"""
        with self._lock:
            self._force_until = max(self._force_until, time.time() + float(seconds))
            self._reopen = True

    def _want_capture_locked(self) -> bool:
        return self._capture_wanted or time.time() < self._force_until

    def get_effects(self) -> dict:
        with self._lock:
            return {
                "rotate": self._rotate,
                "de_red": self._de_red,
                "fps": self._fps,
                "capture": self._want_capture_locked(),
            }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _get_weight(self, h: int, w: int):
        if self._weight is None or self._weight_shape != (h, w):
            self._weight = build_edge_weight(h, w)
            self._weight_shape = (h, w)
        return self._weight

    def _loop(self) -> None:
        while self._running:
            with self._lock:
                if not self._want_capture_locked():
                    pass_wait = True
                else:
                    pass_wait = False
                    fps = self._fps
                    self._reopen = False
            if pass_wait:
                time.sleep(0.25)
                continue
            pipeline = build_gst_pipeline(fps)
            interval = 1.0 / fps
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                LOG.error("打不开摄像头，3 秒后重试")
                time.sleep(3)
                continue
            LOG.info("CameraSource 已打开 fps=%s", fps)
            fail = 0
            try:
                while self._running:
                    with self._lock:
                        if not self._want_capture_locked():
                            LOG.info("无预览/录像/算法需求，暂停摄像头")
                            break
                        if self._reopen:
                            LOG.info("参数变更，重开摄像头")
                            break
                        strength = self._de_red / 100.0
                        rot = self._rotate
                        interval = 1.0 / max(5.0, self._fps)
                    t0 = time.time()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        fail += 1
                        if fail >= 30:
                            LOG.warning("连续读帧失败，重开摄像头")
                            break
                        time.sleep(0.05)
                        continue
                    fail = 0
                    if strength > 0:
                        h, w = frame.shape[:2]
                        frame = correct_red_edge(frame, strength, self._get_weight(h, w))
                    frame = rotate_frame(frame, rot)
                    self.hub.publish(frame, {"rotate": rot, "de_red": int(strength * 100), "fps": fps})
                    elapsed = time.time() - t0
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
            finally:
                cap.release()
                LOG.info("CameraSource 已关闭")
            if self._running:
                time.sleep(0.3)
