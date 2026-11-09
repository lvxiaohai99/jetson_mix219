from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

FrameCallback = Callable[[np.ndarray, dict[str, Any]], None]


class FrameHub:
    """最新帧分发中心：预览/录制/算法都从这里订阅。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._meta: dict[str, Any] = {}
        self._seq = 0
        self._subscribers: dict[str, FrameCallback] = {}

    def publish(self, frame: np.ndarray, meta: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._frame = frame
            self._meta = dict(meta or {})
            self._meta["ts"] = time.time()
            self._meta["seq"] = self._seq
            self._seq += 1
            subs = list(self._subscribers.items())
            snap = frame
            meta_snap = dict(self._meta)
        for _name, cb in subs:
            try:
                cb(snap, meta_snap)
            except Exception:
                # 单个消费者异常不影响其他
                continue

    def get_latest(self) -> tuple[np.ndarray | None, dict[str, Any]]:
        with self._lock:
            if self._frame is None:
                return None, {}
            return self._frame.copy(), dict(self._meta)

    def subscribe(self, name: str, callback: FrameCallback) -> None:
        with self._lock:
            self._subscribers[name] = callback

    def unsubscribe(self, name: str) -> None:
        with self._lock:
            self._subscribers.pop(name, None)

    def has_frame(self) -> bool:
        with self._lock:
            return self._frame is not None
