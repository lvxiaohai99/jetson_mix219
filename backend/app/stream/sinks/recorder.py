from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from backend.app.config import H264_CRF, RECORD_DIR
from backend.app.stream.effects import draw_osd
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.recorder")


class FFmpegH264Writer:
    """边录边可预览：写入分片 MP4；停止后再封装成带 moov 的标准文件。"""

    def __init__(self, path: Path, width: int, height: int, fps: float = 15.0, crf: int = H264_CRF) -> None:
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.final_path = path
        self.part_path = path.with_suffix(".part.mp4")
        # 分片 MP4：录制过程中就有 moov，浏览器/ffprobe 可读
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-g", "30",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            str(self.part_path),
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )

    def write(self, frame: np.ndarray) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("录像管道已关闭")
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        self.proc.stdin.write(frame.tobytes())

    def release(self) -> Path | None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

        if not self.part_path.exists() or self.part_path.stat().st_size <= 0:
            LOG.warning("录像临时文件无效: %s", self.part_path)
            return None

        # 封装成标准 MP4（moov 在文件头），方便网页预览与拖动进度条
        try:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.part_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(self.final_path),
            ]
            subprocess.check_call(cmd, timeout=120)
            self.part_path.unlink(missing_ok=True)
            LOG.info("录像已封装: %s", self.final_path)
            return self.final_path
        except Exception as e:
            LOG.warning("封装失败，保留分片文件供预览: %s", e)
            # 回退：把 part 改名为最终文件（分片格式，多数浏览器仍可播）
            try:
                if self.final_path.exists():
                    self.final_path.unlink()
                self.part_path.rename(self.final_path)
                return self.final_path
            except Exception as e2:
                LOG.error("保留录像失败: %s", e2)
                return None


class RecorderSink:
    def __init__(self, hub: FrameHub) -> None:
        self.hub = hub
        self._lock = threading.Lock()
        self._recording = False
        self._writer: FFmpegH264Writer | None = None
        self._path: Path | None = None
        self._osd = True
        self._fps = 15.0
        self._auto = False
        self._hold_off = False  # 手动停止后暂不自动续录
        self._segment_key: str | None = None  # YYYYMMDD_HH 按小时切片

    def set_osd(self, enabled: bool) -> None:
        with self._lock:
            self._osd = bool(enabled)

    def set_fps(self, fps: float) -> None:
        with self._lock:
            self._fps = float(max(5.0, min(30.0, fps)))

    def set_auto(self, enabled: bool) -> None:
        with self._lock:
            self._auto = bool(enabled)
            if enabled:
                self._hold_off = False

    def start(self) -> None:
        self.hub.subscribe("recorder", self._on_frame)

    def stop(self) -> None:
        self.hub.unsubscribe("recorder")
        self.stop_recording()

    def info(self) -> dict:
        with self._lock:
            return {
                "recording": self._recording,
                "file": self._path.name if self._path else None,
                "auto": self._auto,
                "hold_off": self._hold_off,
            }

    def start_recording(self) -> Path:
        frame, _ = self.hub.get_latest()
        if frame is None:
            raise RuntimeError("摄像头还没有画面")
        with self._lock:
            if self._recording:
                raise RuntimeError("已经在录制中")
            self._hold_off = False
            return self._open_writer_locked(frame)

    def stop_recording(self) -> Path | None:
        with self._lock:
            # 手动停止时，即使开了自动录制也不立刻续开
            if self._auto:
                self._hold_off = True
            return self._close_writer_locked()

    def ensure_auto_recording(self) -> None:
        """启动后若开启自动录制且当前未录，则开始。"""
        frame, _ = self.hub.get_latest()
        if frame is None:
            return
        with self._lock:
            if not self._auto or self._hold_off or self._recording:
                return
            self._open_writer_locked(frame)

    def _open_writer_locked(self, frame: np.ndarray) -> Path:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        stamp = now.strftime("%Y%m%d_%H%M%S")
        path = RECORD_DIR / f"record_{stamp}.mp4"
        h, w = frame.shape[:2]
        self._writer = FFmpegH264Writer(path, w, h, fps=self._fps)
        self._path = path
        self._recording = True
        self._segment_key = now.strftime("%Y%m%d_%H")
        LOG.info("开始录制: %s (临时 %s)", path, self._writer.part_path)
        return path

    def _close_writer_locked(self) -> Path | None:
        if not self._recording:
            return None
        path = self._path
        writer = self._writer
        self._writer = None
        self._recording = False
        self._path = None
        self._segment_key = None
        final = None
        if writer is not None:
            final = writer.release()
        LOG.info("停止录制: %s", final or path)
        return final or path

    def _on_frame(self, frame: np.ndarray, _meta: dict) -> None:
        with self._lock:
            auto = self._auto
            hold_off = self._hold_off
            recording = self._recording
            osd = self._osd
            writer = self._writer
            seg = self._segment_key

        now = datetime.now()
        key = now.strftime("%Y%m%d_%H")

        # 自动录制：未录且未手动暂停则开；跨小时切片
        if auto and not hold_off and not recording:
            with self._lock:
                if self._auto and not self._hold_off and not self._recording:
                    self._open_writer_locked(frame)
                writer = self._writer
                osd = self._osd
            recording = writer is not None

        if auto and not hold_off and recording and seg and key != seg:
            with self._lock:
                self._close_writer_locked()
                self._open_writer_locked(frame)
                writer = self._writer
                osd = self._osd

        if writer is None:
            return
        out = draw_osd(frame.copy()) if osd else frame
        try:
            writer.write(out)
        except Exception as e:
            LOG.warning("写录像失败: %s", e)
            self.stop_recording()
