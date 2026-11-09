from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from backend.app.config import ALGO_OBJECT_MODELS, ROOT_DIR
from backend.app.stream.hub import FrameHub

LOG = logging.getLogger("webcam.algo")

HAAR_CANDIDATES = [
    Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
    Path("/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml"),
]
MODEL_DIR = ROOT_DIR / "models"
TORCH_CACHE = Path.home() / ".cache/torch/hub/checkpoints"
YOLO_LOCAL = MODEL_DIR / "yolov8n.pt"
FRCNN_LOCAL = MODEL_DIR / "fasterrcnn_mobilenet_v3_large_320_fpn.pth"
FRCNN_CACHE_NAME = "fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth"
SSDLITE_LOCAL = MODEL_DIR / "ssdlite320_mobilenet_v3_large_coco.pth"
SSDLITE_CACHE_NAME = "ssdlite320_mobilenet_v3_large_coco-a79551df.pth"

# 预览框颜色（BGR）
COLOR_FACE = (0, 220, 80)
COLOR_OBJ = (0, 180, 255)


def _fmt_label(name: str, score: float) -> str:
    """画面标签：短词大写 + 置信度，例如 TV 0.82。"""
    show = name.upper() if name.isalpha() and len(name) <= 3 else name
    return f"{show} {score:.2f}"


def _seed_torch_cache(local: Path, cache_name: str) -> None:
    if not local.exists():
        return
    TORCH_CACHE.mkdir(parents=True, exist_ok=True)
    dest = TORCH_CACHE / cache_name
    if not dest.exists() or dest.stat().st_size != local.stat().st_size:
        dest.write_bytes(local.read_bytes())


def _nms_same_class(dets: list[dict], iou_thr: float = 0.45, limit: int = 12) -> list[dict]:
    dets = sorted(dets, key=lambda d: d["score"], reverse=True)
    kept: list[dict] = []
    for d in dets:
        ox, oy, ow, oh = d["x"], d["y"], d["w"], d["h"]
        dup = False
        for k in kept:
            if k["class_name"] != d["class_name"]:
                continue
            ix1, iy1 = max(ox, k["x"]), max(oy, k["y"])
            ix2, iy2 = min(ox + ow, k["x"] + k["w"]), min(oy + oh, k["y"] + k["h"])
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            union = ow * oh + k["w"] * k["h"] - inter
            if union > 0 and inter / union > iou_thr:
                dup = True
                break
        if not dup:
            kept.append(d)
        if len(kept) >= limit:
            break
    return kept


class FaceTracker:
    """极简 IOU 跟踪：给人脸分配稳定编号。"""

    def __init__(self, iou_thresh: float = 0.3, max_miss: int = 12) -> None:
        self.iou_thresh = iou_thresh
        self.max_miss = max_miss
        self._next_id = 1
        self._tracks: dict[int, dict] = {}

    @staticmethod
    def _iou(a, b) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        if inter <= 0:
            return 0.0
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    def update(self, boxes: list[tuple[int, int, int, int]], prefix: str = "Face") -> list[dict]:
        assigned: dict[int, tuple[int, int, int, int]] = {}
        used_tracks: set[int] = set()
        for box in boxes:
            best_id, best_iou = None, 0.0
            for tid, tr in self._tracks.items():
                if tid in used_tracks:
                    continue
                iou = self._iou(box, tr["box"])
                if iou > best_iou:
                    best_id, best_iou = tid, iou
            if best_id is not None and best_iou >= self.iou_thresh:
                assigned[best_id] = box
                used_tracks.add(best_id)
            else:
                tid = self._next_id
                self._next_id += 1
                assigned[tid] = box
                used_tracks.add(tid)

        alive = {}
        for tid, tr in self._tracks.items():
            if tid in assigned:
                alive[tid] = {"box": assigned[tid], "miss": 0, "name": tr.get("name", prefix)}
            else:
                miss = tr["miss"] + 1
                if miss <= self.max_miss:
                    alive[tid] = {"box": tr["box"], "miss": miss, "name": tr.get("name", prefix)}
        for tid, box in assigned.items():
            if tid not in alive:
                alive[tid] = {"box": box, "miss": 0, "name": prefix}
        self._tracks = alive
        return [
            {
                "id": tid,
                "x": b["box"][0],
                "y": b["box"][1],
                "w": b["box"][2],
                "h": b["box"][3],
                "label": f"{b.get('name', prefix)}-{tid}",
                "kind": "face",
            }
            for tid, b in sorted(self._tracks.items())
            if b["miss"] == 0
        ]


class AlgorithmSink:
    """算法消费者：人脸 / 物体识别，验证 Source→Hub→Algo（不另开摄像头）。"""

    def __init__(self, hub: FrameHub) -> None:
        self.hub = hub
        self._lock = threading.Lock()
        self.mode = "off"  # off | face | object
        self._frames = 0
        self._infer_count = 0
        self._last_ms = 0.0
        self._dets: list[dict] = []
        self._tracker = FaceTracker()
        self._cascade = None
        self._face_every_n = 2
        self._obj_every_n = 3  # YOLO 较快；若回退 FRCNN 会自动放慢
        self._face_scale = 0.5
        # YOLO 默认约 0.25；0.45 只会留下很有把握的少数框
        self._obj_score = 0.25
        self._obj_min_area_ratio = 0.004
        self._obj_model = None
        self._obj_weights = None
        self._obj_backend = None  # yolo | torchvision
        self._obj_device = "cpu"
        self._obj_name = None
        self._obj_model_pref = "auto"  # auto | yolov8n | fasterrcnn | ssdlite
        self._obj_load_gen = 0  # 切换模型时递增，丢弃过期加载结果
        self._obj_load_error: str | None = None
        # 推理放到后台线程，避免堵死 Hub→预览（YOLO 单帧可到几百 ms）
        self._pending: tuple[np.ndarray, dict, str, int] | None = None
        self._wake = threading.Event()
        self._worker_running = False
        self._worker: threading.Thread | None = None
        self._busy = False
        self._load_cascade()

    @staticmethod
    def available_models() -> dict:
        """给前端/API：每个模型是否已下载到 models/。"""
        out = {}
        for key, meta in ALGO_OBJECT_MODELS.items():
            fname = meta.get("file")
            path = (MODEL_DIR / fname) if fname else None
            ready = True if key == "auto" else bool(path and path.exists() and path.stat().st_size > 100_000)
            out[key] = {
                "label": meta.get("label") or key,
                "file": fname,
                "ready": ready,
                "path": str(path) if path else None,
            }
        return out

    def _load_cascade(self) -> None:
        for p in HAAR_CANDIDATES:
            if p.exists():
                c = cv2.CascadeClassifier(str(p))
                if not c.empty():
                    self._cascade = c
                    LOG.info("人脸模型已加载: %s", p)
                    return
        LOG.warning("未找到 Haar 人脸模型")

    def _clear_object_model(self) -> None:
        with self._lock:
            self._obj_model = None
            self._obj_weights = None
            self._obj_backend = None
            self._obj_name = None
            self._obj_load_error = None
            self._dets = []

    def set_object_model(self, pref: str) -> None:
        """切换物体识别模型：auto | yolov8n | fasterrcnn | ssdlite。"""
        pref = str(pref or "auto").lower()
        if pref not in ALGO_OBJECT_MODELS:
            pref = "auto"
        with self._lock:
            same = pref == self._obj_model_pref and self._obj_model is not None and not self._obj_load_error
            self._obj_model_pref = pref
            if not same:
                self._obj_load_gen += 1
        if same:
            return
        self._clear_object_model()
        LOG.info("物体识别模型偏好: %s", pref)
        if self.mode == "object":
            threading.Thread(target=self._ensure_object_model, name="load-obj", daemon=True).start()

    def _try_load_yolo(self, device: str):
        if not (YOLO_LOCAL.exists() and YOLO_LOCAL.stat().st_size > 1_000_000):
            raise FileNotFoundError(f"缺少模型文件: {YOLO_LOCAL}")
        from ultralytics import YOLO

        model = YOLO(str(YOLO_LOCAL))
        model.predict(np.zeros((320, 320, 3), dtype=np.uint8), conf=0.5, verbose=False, device=device)
        return model, None, "yolo", "yolov8n", 2

    def _try_load_fasterrcnn(self, device: str):
        from torchvision.models.detection import (
            FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
            fasterrcnn_mobilenet_v3_large_320_fpn,
        )

        _seed_torch_cache(FRCNN_LOCAL, FRCNN_CACHE_NAME)
        weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
        model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights)
        model.eval()
        model.to(device)
        return model, weights, "torchvision", "fasterrcnn_mobilenet_v3_320", 5

    def _try_load_ssdlite(self, device: str):
        from torchvision.models.detection import (
            SSDLite320_MobileNet_V3_Large_Weights,
            ssdlite320_mobilenet_v3_large,
        )

        _seed_torch_cache(SSDLITE_LOCAL, SSDLITE_CACHE_NAME)
        weights = SSDLite320_MobileNet_V3_Large_Weights.DEFAULT
        model = ssdlite320_mobilenet_v3_large(weights=weights)
        model.eval()
        model.to(device)
        return model, weights, "torchvision", "ssdlite320_coco", 4

    def _ensure_object_model(self) -> bool:
        with self._lock:
            if self._obj_model is not None:
                return True
            if self._obj_load_error:
                return False
            pref = self._obj_model_pref
            gen = self._obj_load_gen
        try:
            device = "cpu"
            loaders = {
                "yolov8n": [self._try_load_yolo],
                "fasterrcnn": [self._try_load_fasterrcnn],
                "ssdlite": [self._try_load_ssdlite],
                "auto": [self._try_load_yolo, self._try_load_fasterrcnn, self._try_load_ssdlite],
            }
            errors: list[str] = []
            loaded = None
            for loader in loaders.get(pref, loaders["auto"]):
                with self._lock:
                    if gen != self._obj_load_gen:
                        return False
                try:
                    loaded = loader(device)
                    break
                except Exception as e:
                    errors.append(f"{loader.__name__}: {e}")
                    LOG.warning("模型加载失败 %s", e)
            if loaded is None:
                raise RuntimeError("；".join(errors) or f"无法加载模型 pref={pref}")

            model, weights, backend, name, every_n = loaded
            with self._lock:
                if gen != self._obj_load_gen:
                    LOG.info("丢弃过期模型加载结果 pref=%s name=%s", pref, name)
                    return False
                self._obj_model = model
                self._obj_weights = weights
                self._obj_backend = backend
                self._obj_device = device
                self._obj_name = name
                self._obj_every_n = every_n
                self._obj_load_error = None
            LOG.info("物体识别模型已加载 pref=%s name=%s backend=%s", pref, name, backend)
            return True
        except Exception as e:
            LOG.warning("物体识别模型加载失败: %s", e)
            with self._lock:
                if gen == self._obj_load_gen:
                    self._obj_load_error = str(e)
            return False

    def start(self) -> None:
        self._worker_running = True
        self._worker = threading.Thread(target=self._worker_loop, name="algo-infer", daemon=True)
        self._worker.start()
        self.hub.subscribe("algorithm", self._on_frame)
        LOG.info("AlgoSink 已挂载 mode=%s（异步推理）", self.mode)

    def stop(self) -> None:
        self.hub.unsubscribe("algorithm")
        self._worker_running = False
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

    def set_mode(self, mode: str) -> None:
        mode = str(mode or "off").lower()
        if mode not in ("off", "face", "object"):
            mode = "off"
        with self._lock:
            self.mode = mode
            self._dets = []
            self._tracker.reset()
            if mode == "object" and self._obj_model is None:
                self._obj_load_error = None  # 允许重新尝试加载
        if mode == "object":
            # 后台预加载，避免卡第一帧
            threading.Thread(target=self._ensure_object_model, name="load-obj", daemon=True).start()
        LOG.info("算法模式: %s", mode)

    def set_enabled(self, enabled: bool) -> None:
        """兼容旧接口：True→face，False→off。"""
        self.set_mode("face" if enabled else "off")

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "enabled": self.mode != "off",
                "face_detect": self.mode == "face",
                "algo_model": self._obj_model_pref,
                "model": (
                    "haar_frontalface"
                    if self.mode == "face"
                    else (self._obj_name if self.mode == "object" else None)
                ),
                "model_loaded": self._obj_name,
                "score_threshold": self._obj_score if self.mode == "object" else None,
                "detections": list(self._dets),
                "faces": [d for d in self._dets if d.get("kind") == "face"],
                "face_count": sum(1 for d in self._dets if d.get("kind") == "face"),
                "object_count": sum(1 for d in self._dets if d.get("kind") == "object"),
                "det_count": len(self._dets),
                "infer_count": self._infer_count,
                "last_infer_ms": round(self._last_ms, 1),
                "frames_seen": self._frames,
                "object_device": self._obj_device,
                "object_load_error": self._obj_load_error,
                "algo_models": self.available_models(),
            }

    def annotate(self, frame: np.ndarray) -> np.ndarray:
        with self._lock:
            if self.mode == "off" or not self._dets:
                return frame
            dets = list(self._dets)
        out = frame
        for d in dets:
            x, y, w, h = int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])
            color = COLOR_FACE if d.get("kind") == "face" else COLOR_OBJ
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
            label = str(d.get("label") or "obj")
            cv2.putText(
                out, label, (x, max(16, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA,
            )
        return out

    def _on_frame(self, frame: np.ndarray, meta: dict) -> None:
        """只投递最新帧，绝不在 Hub 回调里做重推理。"""
        with self._lock:
            self._frames += 1
            mode = self.mode
            frames = self._frames
            busy = self._busy
            face_n = self._face_every_n
            obj_n = self._obj_every_n
        if mode == "off":
            return
        every_n = face_n if mode == "face" else obj_n
        if frames % every_n != 1:
            return
        # 上一帧还在推，直接丢弃中间帧，保证预览流畅
        if busy:
            return
        with self._lock:
            self._pending = (frame.copy(), dict(meta or {}), mode, frames)
        self._wake.set()

    def _worker_loop(self) -> None:
        while self._worker_running:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            with self._lock:
                job = self._pending
                self._pending = None
                if job is None:
                    continue
                self._busy = True
            try:
                frame, meta, mode, frames = job
                if mode == "face":
                    self._infer_face(frame, meta, frames)
                elif mode == "object":
                    self._infer_object(frame, meta, frames)
            except Exception as e:
                LOG.warning("算法推理线程异常: %s", e)
            finally:
                with self._lock:
                    self._busy = False

    def _infer_face(self, frame: np.ndarray, meta: dict, frames: int) -> None:
        with self._lock:
            cascade = self._cascade
            scale = self._face_scale
        if cascade is None:
            return
        t0 = time.time()
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        rects = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40), flags=cv2.CASCADE_SCALE_IMAGE
        )
        inv = 1.0 / scale if scale > 0 else 1.0
        boxes = [(int(x * inv), int(y * inv), int(bw * inv), int(bh * inv)) for (x, y, bw, bh) in rects]
        dets = self._tracker.update(boxes, prefix="Face")
        ms = (time.time() - t0) * 1000.0
        with self._lock:
            self._dets = dets
            self._infer_count += 1
            self._last_ms = ms
        if self._infer_count % 30 == 1:
            LOG.info("人脸检测 faces=%s infer_ms=%.1f seq=%s", len(dets), ms, meta.get("seq"))

    def _infer_object(self, frame: np.ndarray, meta: dict, frames: int) -> None:
        if not self._ensure_object_model():
            return

        with self._lock:
            model = self._obj_model
            weights = self._obj_weights
            backend = self._obj_backend
            device = self._obj_device
            score_thr = self._obj_score
            min_area_ratio = self._obj_min_area_ratio

        t0 = time.time()
        h, w = frame.shape[:2]
        frame_area = float(max(1, h * w))
        dets: list[dict] = []

        if backend == "yolo":
            results = model.predict(
                frame, conf=score_thr, iou=0.45, imgsz=640, verbose=False, device=device
            )
            r0 = results[0] if results else None
            names = getattr(model, "names", {}) or {}
            if r0 is not None and r0.boxes is not None and len(r0.boxes):
                xyxy = r0.boxes.xyxy.cpu().numpy()
                confs = r0.boxes.conf.cpu().numpy()
                clss = r0.boxes.cls.cpu().numpy().astype(int)
                for i, sc_f in enumerate(confs):
                    sc_f = float(sc_f)
                    x1, y1, x2, y2 = xyxy[i]
                    x1 = int(max(0, min(w - 1, x1)))
                    y1 = int(max(0, min(h - 1, y1)))
                    x2 = int(max(0, min(w - 1, x2)))
                    y2 = int(max(0, min(h - 1, y2)))
                    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
                    if (bw * bh) / frame_area < min_area_ratio:
                        continue
                    name = str(names.get(int(clss[i]), clss[i]))
                    dets.append(
                        {
                            "id": i + 1,
                            "x": x1,
                            "y": y1,
                            "w": bw,
                            "h": bh,
                            "label": _fmt_label(name, sc_f),
                            "kind": "object",
                            "score": sc_f,
                            "class_name": name,
                        }
                    )
        else:
            try:
                import torch
                from PIL import Image
            except Exception as e:
                LOG.warning("物体识别依赖不可用: %s", e)
                return
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            tensor = weights.transforms()(Image.fromarray(rgb)).to(device)
            with torch.inference_mode():
                out = model([tensor])[0]
            boxes = out["boxes"].detach().cpu().numpy()
            labels = out["labels"].detach().cpu().numpy()
            scores = out["scores"].detach().cpu().numpy()
            cats = weights.meta["categories"]
            for i, sc in enumerate(scores):
                sc_f = float(sc)
                if sc_f < score_thr:
                    continue
                x1, y1, x2, y2 = boxes[i]
                x1 = int(max(0, min(w - 1, x1)))
                y1 = int(max(0, min(h - 1, y1)))
                x2 = int(max(0, min(w - 1, x2)))
                y2 = int(max(0, min(h - 1, y2)))
                bw, bh = max(1, x2 - x1), max(1, y2 - y1)
                if (bw * bh) / frame_area < min_area_ratio:
                    continue
                name = cats[int(labels[i])] if int(labels[i]) < len(cats) else str(int(labels[i]))
                if name in ("N/A", "__background__"):
                    continue
                dets.append(
                    {
                        "id": i + 1,
                        "x": x1,
                        "y": y1,
                        "w": bw,
                        "h": bh,
                        "label": _fmt_label(name, sc_f),
                        "kind": "object",
                        "score": sc_f,
                        "class_name": name,
                    }
                )

        dets = _nms_same_class(dets)
        ms = (time.time() - t0) * 1000.0
        with self._lock:
            self._dets = dets
            self._infer_count += 1
            self._last_ms = ms
        if self._infer_count % 20 == 1:
            LOG.info(
                "物体识别 model=%s dets=%s infer_ms=%.1f seq=%s",
                self._obj_name, len(dets), ms, meta.get("seq"),
            )
