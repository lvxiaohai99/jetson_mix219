from __future__ import annotations

import logging

import numpy as np

from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.algo")


class AlgorithmSink:
    """算法消费者桩：以后在这里接检测/识别，不必再开摄像头。"""

    def __init__(self, hub: FrameHub) -> None:
        self.hub = hub
        self.enabled = False
        self._frames = 0

    def start(self) -> None:
        self.hub.subscribe("algorithm", self._on_frame)
        LOG.info("AlgoSink 已挂载（默认不处理，enabled=False）")

    def stop(self) -> None:
        self.hub.unsubscribe("algorithm")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def _on_frame(self, frame: np.ndarray, meta: dict) -> None:
        if not self.enabled:
            return
        self._frames += 1
        # 预留：在此调用模型推理
        if self._frames % 150 == 1:
            h, w = frame.shape[:2]
            LOG.info("AlgoSink 收到帧 seq=%s size=%sx%s", meta.get("seq"), w, h)
