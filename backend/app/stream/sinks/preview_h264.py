from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable

import numpy as np

from backend.app.config import H264_CRF
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.preview.h264")


class PreviewH264Sink:
    """订阅 Hub 帧，用 libx264 编成 MPEG-TS，广播给 WebSocket 客户端。"""

    def __init__(self, hub: FrameHub, fps: float = 15.0, crf: int = H264_CRF) -> None:
        self.hub = hub
        self.fps = float(fps)
        self.crf = crf
        self._lock = threading.Lock()
        self._clients: list[Callable[[bytes], None]] = []
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._size: tuple[int, int] | None = None
        self._active = False
        self._fps = 0.0
        self._frames = 0
        self._fps_t0 = time.time()

    def set_fps(self, fps: float) -> None:
        new_fps = float(max(5.0, min(30.0, fps)))
        with self._lock:
            if abs(new_fps - self.fps) < 0.1:
                return
            self.fps = new_fps
        # 尺寸变化以外也强制重建编码器
        self._stop_encoder()


    def start(self) -> None:
        self.hub.subscribe("preview_h264", self._on_frame)

    def stop(self) -> None:
        self.hub.unsubscribe("preview_h264")
        self._stop_encoder()

    def add_client(self, send_bytes: Callable[[bytes], None]) -> None:
        with self._lock:
            self._clients.append(send_bytes)
            self._active = True

    def remove_client(self, send_bytes: Callable[[bytes], None]) -> None:
        with self._lock:
            self._clients = [c for c in self._clients if c is not send_bytes]
            if not self._clients:
                self._active = False

    def stats(self) -> dict:
        with self._lock:
            return {
                "encoder": "libx264",
                "fps": round(self._fps, 1),
                "clients": len(self._clients),
                "ready": self._proc is not None,
            }

    def _on_frame(self, frame: np.ndarray, _meta: dict) -> None:
        with self._lock:
            if not self._active or not self._clients:
                return
            clients = list(self._clients)
        h, w = frame.shape[:2]
        w -= w % 2
        h -= h % 2
        if frame.shape[1] != w or frame.shape[0] != h:
            import cv2

            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        self._ensure_encoder(w, h)
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(frame.tobytes())
        except Exception as e:
            LOG.warning("写入 H264 编码器失败: %s", e)
            self._stop_encoder()
            return
        with self._lock:
            self._frames += 1
            now = time.time()
            if now - self._fps_t0 >= 1.0:
                self._fps = self._frames / (now - self._fps_t0)
                self._frames = 0
                self._fps_t0 = now

    def _ensure_encoder(self, w: int, h: int) -> None:
        if self._proc is not None and self._size == (w, h):
            return
        self._stop_encoder()
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}", "-r", str(self.fps),
            "-i", "pipe:0",
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-crf", str(self.crf),
            "-pix_fmt", "yuv420p",
            "-f", "mpegts",
            "pipe:1",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._size = (w, h)
        self._reader = threading.Thread(target=self._broadcast_loop, name="h264-broadcast", daemon=True)
        self._reader.start()
        LOG.info("H264 预览编码器启动 %sx%s", w, h)

    def _broadcast_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                with self._lock:
                    clients = list(self._clients)
                dead = []
                for send in clients:
                    try:
                        send(chunk)
                    except Exception:
                        dead.append(send)
                if dead:
                    with self._lock:
                        self._clients = [c for c in self._clients if c not in dead]
                        if not self._clients:
                            self._active = False
        finally:
            LOG.info("H264 广播线程结束")

    def _stop_encoder(self) -> None:
        proc = self._proc
        self._proc = None
        self._size = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
