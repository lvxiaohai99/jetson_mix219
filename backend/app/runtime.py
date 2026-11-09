from __future__ import annotations

from dataclasses import dataclass

from backend.app.stream.hub import FrameHub
from backend.app.stream.sinks.algorithm import AlgorithmSink
from backend.app.stream.sinks.preview_h264 import PreviewH264Sink
from backend.app.stream.sinks.preview_mjpeg import PreviewMjpegSink
from backend.app.stream.sinks.recorder import RecorderSink
from backend.app.stream.source import CameraSource


@dataclass
class AppRuntime:
    hub: FrameHub
    source: CameraSource
    preview_mjpeg: PreviewMjpegSink
    preview_h264: PreviewH264Sink
    recorder: RecorderSink
    algorithm: AlgorithmSink


def create_runtime() -> AppRuntime:
    hub = FrameHub()
    source = CameraSource(hub)
    preview_mjpeg = PreviewMjpegSink(hub)
    preview_h264 = PreviewH264Sink(hub)
    recorder = RecorderSink(hub)
    algorithm = AlgorithmSink(hub)
    return AppRuntime(
        hub=hub,
        source=source,
        preview_mjpeg=preview_mjpeg,
        preview_h264=preview_h264,
        recorder=recorder,
        algorithm=algorithm,
    )
