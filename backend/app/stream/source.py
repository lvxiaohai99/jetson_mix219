from __future__ import annotations

import logging
import threading
import time

import cv2

from backend.app.config import build_gst_pipeline
from backend.app.stream.effects import build_edge_weight, correct_red_edge, rotate_frame
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.source")


def _cma_free_kb() -> int | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CmaFree:"):
                    return int(line.split()[1])
    except Exception:
        return None
    return None


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
        # 摄像头健康：ready / starting / paused / open_failed / no_frame / argus_busy / cma_low
        self._state = "starting"
        self._detail = ""
        self._last_frame_ts = 0.0
        self._open_fail_streak = 0
        self._read_fail_cycles = 0
        self._recovering = False
        self._pipeline_held = False
        self._released_evt = threading.Event()
        self._released_evt.set()

    def _set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            if state != self._state or detail != self._detail:
                LOG.info("摄像头状态: %s%s", state, f" ({detail})" if detail else "")
            self._state = state
            self._detail = detail

    def get_status(self) -> dict:
        with self._lock:
            age = (time.time() - self._last_frame_ts) if self._last_frame_ts else None
            return {
                "state": self._state,
                "detail": self._detail,
                "capture_wanted": self._want_capture_locked(),
                "last_frame_age_s": round(age, 1) if age is not None else None,
                "open_fail_streak": self._open_fail_streak,
                "read_fail_cycles": self._read_fail_cycles,
                "hint": self._hint_locked(),
            }

    def _hint_locked(self) -> str:
        st = self._state
        if st == "cma_low":
            return "CMA 连续内存不足，请点页面「恢复摄像头」"
        if st == "argus_busy":
            return "Argus 摄像头占用/卡死，请点页面「恢复摄像头」"
        if st == "open_failed":
            return "打不开摄像头，检查排线/驱动，或点「恢复摄像头」"
        if st == "no_frame":
            return "能打开但读不到帧，多为 Argus 异常，请点「恢复摄像头」"
        if st == "paused":
            return "预览与录像均未开启，采集已暂停"
        if st == "starting":
            return "正在等待首帧…"
        return ""

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
                # 关闭时即便状态未变也强制打断读循环，确保 release
                if not wanted:
                    self._reopen = True
                return
            self._capture_wanted = wanted
            if wanted:
                self._reopen = True  # 尽快重新打开
                if self._state == "paused":
                    self._state = "starting"
                    self._detail = "恢复采集"
            else:
                self._reopen = True  # 打断当前 cap.read 循环并走 finally:release
                self._state = "paused"
                self._detail = "无预览/录像需求"
            LOG.info("摄像头采集: %s", "需要" if wanted else "暂停")

    def request_brief_capture(self, seconds: float = 3.0) -> None:
        """截图等短时需求：临时拉起采集。"""
        with self._lock:
            self._force_until = max(self._force_until, time.time() + float(seconds))
            self._reopen = True

    def pause_and_release(self, timeout: float = 6.0) -> bool:
        """关闭预览时：停采并等到管道真正 release（尽量让 Argus 松手）。"""
        with self._lock:
            self._capture_wanted = False
            self._force_until = 0.0
            self._recovering = True
            self._reopen = True
            self._state = "paused"
            self._detail = "正在释放摄像头管道…"
            if not self._pipeline_held:
                self._released_evt.set()
            else:
                self._released_evt.clear()
        ok = self._released_evt.wait(timeout=max(1.0, timeout))
        with self._lock:
            self._recovering = False
            self._state = "paused"
            self._detail = "采集已释放" if ok else "释放超时，管道可能仍被 Argus 占用"
        LOG.info("pause_and_release: released=%s", ok)
        return ok

    def begin_recover(self, timeout: float = 5.0) -> bool:
        """暂停取流并等到 GStreamer/Argus 管道释放。"""
        with self._lock:
            self._recovering = True
            self._reopen = True
            self._state = "starting"
            self._detail = "正在释放摄像头…"
            held = self._pipeline_held
            if not held:
                self._released_evt.set()
            else:
                self._released_evt.clear()
        ok = self._released_evt.wait(timeout=max(1.0, timeout))
        LOG.info("begin_recover: released=%s", ok)
        return ok

    def end_recover(self) -> None:
        """恢复取流并清失败计数。"""
        with self._lock:
            self._recovering = False
            self._open_fail_streak = 0
            self._read_fail_cycles = 0
            self._reopen = True
            self._state = "starting"
            self._detail = "恢复后重新打开"
        LOG.info("end_recover: 重新打开摄像头")

    def _want_capture_locked(self) -> bool:
        if self._recovering:
            return False
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

    def _classify_open_fail(self) -> tuple[str, str]:
        cma = _cma_free_kb()
        if cma is not None and cma < 8 * 1024:
            return "cma_low", f"CmaFree={cma}kB"
        if self._open_fail_streak >= 3 or self._read_fail_cycles >= 3:
            return "argus_busy", "连续打开/读帧失败，疑似 Argus 占用"
        return "open_failed", "VideoCapture 未打开"

    def _loop(self) -> None:
        while self._running:
            with self._lock:
                if not self._want_capture_locked():
                    pass_wait = True
                    self._state = "paused"
                    self._detail = "采集暂停"
                else:
                    pass_wait = False
                    fps = self._fps
                    self._reopen = False
                    if self._state in ("paused",):
                        self._state = "starting"
                        self._detail = "正在打开"
            if pass_wait:
                time.sleep(0.25)
                continue
            pipeline = build_gst_pipeline(fps)
            interval = 1.0 / fps
            self._set_state("starting", f"打开管道 fps={fps}")
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                with self._lock:
                    self._open_fail_streak += 1
                    self._pipeline_held = False
                    self._released_evt.set()
                state, detail = self._classify_open_fail()
                self._set_state(state, detail)
                LOG.error("打不开摄像头 (%s)，3 秒后重试", detail)
                time.sleep(3)
                continue
            with self._lock:
                self._open_fail_streak = 0
                self._pipeline_held = True
                self._released_evt.clear()
            LOG.info("CameraSource 已打开 fps=%s", fps)
            fail = 0
            got_frame = False
            try:
                while self._running:
                    with self._lock:
                        if not self._want_capture_locked():
                            LOG.info("无预览/录像/算法需求，暂停摄像头")
                            self._state = "paused"
                            self._detail = "采集暂停"
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
                        if fail >= 10 and not got_frame:
                            # OpenCV 常在 Argus 失败时仍 isOpened=True
                            self._set_state("no_frame", f"连续读帧失败 {fail}")
                        if fail >= 30:
                            with self._lock:
                                self._read_fail_cycles += 1
                            cma = _cma_free_kb()
                            if cma is not None and cma < 8 * 1024:
                                self._set_state("cma_low", f"读帧失败且 CmaFree={cma}kB")
                            elif self._read_fail_cycles >= 2:
                                self._set_state("argus_busy", "多次读帧失败，疑似 Device in use")
                            else:
                                self._set_state("no_frame", "连续读帧失败，重开")
                            LOG.warning("连续读帧失败，重开摄像头")
                            break
                        time.sleep(0.05)
                        continue
                    fail = 0
                    got_frame = True
                    with self._lock:
                        self._read_fail_cycles = 0
                        self._last_frame_ts = time.time()
                        self._state = "ready"
                        self._detail = ""
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
                with self._lock:
                    self._pipeline_held = False
                    self._released_evt.set()
                LOG.info("CameraSource 已关闭")
            if self._running:
                time.sleep(0.3)
