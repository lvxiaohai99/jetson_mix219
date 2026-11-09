from __future__ import annotations

import cv2
import numpy as np


def rotate_frame(frame: np.ndarray, degrees: int) -> np.ndarray:
    degrees = int(degrees) % 360
    if degrees == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def build_edge_weight(h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy = (h - 1) / 2.0
    cx = (w - 1) / 2.0
    ry = np.abs(yy - cy) / max(cy, 1.0)
    rx = np.abs(xx - cx) / max(cx, 1.0)
    r = np.clip(np.sqrt(rx * rx + ry * ry) / 1.35, 0.0, 1.0)
    return (r * r).astype(np.float32)


def correct_red_edge(frame: np.ndarray, strength: float, weight: np.ndarray) -> np.ndarray:
    if strength <= 0.001:
        return frame
    s = float(strength)
    scale_b = np.clip((1.0 + weight * s * 0.06) * 256.0, 0, 512).astype(np.uint16)
    scale_g = np.clip((1.0 + weight * s * 0.10) * 256.0, 0, 512).astype(np.uint16)
    scale_r = np.clip((1.0 - weight * s * 0.42) * 256.0, 0, 512).astype(np.uint16)
    out = frame.astype(np.uint16)
    out[:, :, 0] = np.minimum((out[:, :, 0] * scale_b) >> 8, 255)
    out[:, :, 1] = np.minimum((out[:, :, 1] * scale_g) >> 8, 255)
    out[:, :, 2] = np.minimum((out[:, :, 2] * scale_r) >> 8, 255)
    return out.astype(np.uint8)


def draw_osd(frame: np.ndarray) -> np.ndarray:
    from datetime import datetime

    text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    origin = (16, 40)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return frame
