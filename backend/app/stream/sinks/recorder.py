from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from backend.app.config import H264_CRF, RECORD_DIR
from backend.app.stream.effects import draw_osd
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.recorder")


def remux_preview(part_path: Path, preview_path: Path) -> dict:
    """把当前已写入的分片封装成可拖动进度条的预览 MP4（延迟几秒即可）。"""
    if not part_path.exists() or part_path.stat().st_size < 4096:
        raise RuntimeError("录像还太短，稍后再试")
    tmp = preview_path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(part_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(tmp),
    ]
    subprocess.check_call(cmd, timeout=60)
    if not tmp.exists() or tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("预览封装失败")
    tmp.replace(preview_path)
    duration = 0.0
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(preview_path),
            ],
            text=True,
            timeout=10,
        ).strip()
        duration = float(out)
    except Exception:
        pass
    return {
        "file": preview_path.name,
        "url": f"/files/recordings/{preview_path.name}",
        "size_mb": round(preview_path.stat().st_size / 1024 / 1024, 2),
        "duration": duration,
        "as_of": datetime.now().strftime("%H:%M:%S"),
    }


class FFmpegH264Writer:
    """边录边可预览：写入分片 MP4；定期/按需封装 .preview.mp4；停止后再出正式文件。"""

    def __init__(self, path: Path, width: int, height: int, fps: float = 15.0, crf: int = H264_CRF) -> None:
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.final_path = path
        self.part_path = path.with_suffix(".part.mp4")
        self.preview_path = Path(str(path).replace(".mp4", ".preview.mp4"))
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

        self.preview_path.unlink(missing_ok=True)
        self.preview_path.with_suffix(".tmp.mp4").unlink(missing_ok=True)

        if not self.part_path.exists() or self.part_path.stat().st_size <= 0:
            LOG.warning("录像临时文件无效: %s", self.part_path)
            return None

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
            try:
                if self.final_path.exists():
                    self.final_path.unlink()
                self.part_path.rename(self.final_path)
                return self.final_path
            except Exception as e2:
                LOG.error("保留录像失败: %s", e2)
                return None

    def abort(self) -> None:
        """强制结束并删除临时/目标/预览文件，不做封装。"""
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass
        for p in (self.part_path, self.final_path, self.preview_path, self.preview_path.with_suffix(".tmp.mp4")):
            try:
                p.unlink(missing_ok=True)
            except Exception as e:
                LOG.warning("删除录像文件失败 %s: %s", p, e)


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
        self._hold_off = False
        self._segment_key: str | None = None
        self._preview_lock = threading.Lock()
        self._last_preview: dict | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_stop = threading.Event()

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
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(
            target=self._preview_loop, name="record-preview", daemon=True
        )
        self._preview_thread.start()

    def stop(self) -> None:
        self._preview_stop.set()
        self.hub.unsubscribe("recorder")
        self.stop_recording()

    def info(self) -> dict:
        with self._lock:
            preview = None
            if self._writer is not None:
                preview = self._writer.preview_path.name
            return {
                "recording": self._recording,
                "file": self._path.name if self._path else None,
                "preview_file": preview,
                "auto": self._auto,
                "hold_off": self._hold_off,
                "last_preview": self._last_preview,
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

    def stop_recording(self, *, pause_auto: bool = True) -> Path | None:
        with self._lock:
            if self._auto:
                self._hold_off = bool(pause_auto)
            path = self._close_writer_locked()
            auto = self._auto
            hold = self._hold_off
        if path is not None and auto and not hold:
            try:
                self.ensure_auto_recording()
            except Exception as e:
                LOG.warning("停止后续录失败: %s", e)
        return path

    def matches_active(self, name: str) -> bool:
        with self._lock:
            if not self._recording or self._path is None:
                return False
            base = self._path.name
            preview = self._path.name.replace(".mp4", ".preview.mp4")
            return name in (base, self._path.with_suffix(".part.mp4").name, preview)

    def make_live_preview(self, force: bool = True) -> dict:
        """生成/刷新延迟预览文件，可拖到已录制的时间点。"""
        with self._lock:
            if not self._recording or self._writer is None:
                raise RuntimeError("当前没有在录制")
            part = self._writer.part_path
            preview = self._writer.preview_path
            name = self._path.name if self._path else None

        with self._preview_lock:
            # 若刚生成过且未强制，直接返回缓存（减轻 CPU）
            if (
                not force
                and self._last_preview
                and self._last_preview.get("source") == name
                and preview.exists()
                and time.time() - float(self._last_preview.get("ts", 0)) < 3.0
            ):
                return dict(self._last_preview)

            info = remux_preview(part, preview)
            info["source"] = name
            info["ts"] = time.time()
            info["recording"] = True
            self._last_preview = info
            return dict(info)

    def abort_recording(self, *, resume_auto: bool = True) -> dict:
        with self._lock:
            if not self._recording:
                return {"aborted": False, "restarted": False}
            path = self._path
            writer = self._writer
            auto = self._auto
            self._writer = None
            self._recording = False
            self._path = None
            self._segment_key = None
            self._last_preview = None
            if resume_auto and auto:
                self._hold_off = False
            else:
                self._hold_off = True

        if writer is not None:
            writer.abort()
        LOG.info("已丢弃录像: %s", path)

        restarted = False
        if resume_auto and auto:
            try:
                self.ensure_auto_recording()
                restarted = self.info().get("recording", False)
            except Exception as e:
                LOG.warning("删除后重新自动录制失败: %s", e)
        return {
            "aborted": True,
            "file": path.name if path else None,
            "restarted": restarted,
            "new_file": self.info().get("file"),
        }

    def ensure_auto_recording(self) -> None:
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
        self._last_preview = None
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
        self._last_preview = None
        final = None
        if writer is not None:
            final = writer.release()
        LOG.info("停止录制: %s", final or path)
        return final or path

    def _preview_loop(self) -> None:
        """后台每几秒刷新一次延迟预览，打开日历即可拖看已录部分。"""
        while not self._preview_stop.wait(6.0):
            try:
                with self._lock:
                    active = self._recording and self._writer is not None
                if not active:
                    continue
                self.make_live_preview(force=True)
            except Exception as e:
                LOG.debug("延迟预览刷新跳过: %s", e)

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
