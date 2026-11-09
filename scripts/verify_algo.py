#!/usr/bin/env python3
"""无人脸对着摄像头时，也能做的架构/算法验证。

验证项：
1) 服务健康、Hub 有帧
2) AlgoSink 人脸/物体模式可切换并在推理
3) Haar 对人脸样例图可用；物体模式对当前抓拍可跑通
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "testdata" / "face_sample.jpg"
BASE = "http://127.0.0.1:8080"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=8) as r:
        return json.loads(r.read().decode())


def post(path: str, body: dict):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    fails = 0
    try:
        h = get("/api/health")
    except Exception as e:
        print(f"[FAIL] 服务不可达: {e}")
        print("请先 ./start.sh 启动服务")
        return 1

    fails += not ok("服务健康", bool(h.get("ok")), f"camera={h.get('camera')}")
    fails += not ok("Hub 有预览帧", bool((h.get("mjpeg") or {}).get("ready")), str(h.get("mjpeg")))

    # 人脸模式
    post("/api/algo/mode", {"mode": "face"})
    time.sleep(1.0)
    st = get("/api/algo/status")
    fails += not ok("人脸模式开启", st.get("mode") == "face")
    fails += not ok("AlgoSink 在收帧", int(st.get("frames_seen") or 0) > 0, f"frames={st.get('frames_seen')}")
    fails += not ok("人脸在推理", int(st.get("infer_count") or 0) > 0, f"infer={st.get('infer_count')} ms={st.get('last_infer_ms')}")
    print(f"[INFO] 镜头前人脸数={st.get('face_count')}（没人时为 0 正常）")

    import cv2

    cascade = cv2.CascadeClassifier("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml")
    fails += not ok("Haar 可读", not cascade.empty())
    if SAMPLE.exists():
        img = cv2.imread(str(SAMPLE))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.05, 3, minSize=(20, 20))
        fails += not ok("样例图人脸检测", len(faces) >= 1, f"faces={len(faces)}")
    else:
        print(f"[INFO] 无样例图 {SAMPLE}，跳过离线人脸样例")

    # 物体模式（桌上东西也能验）
    post("/api/algo/mode", {"mode": "object"})
    # 等模型加载 + 几次推理
    ready = False
    st = {}
    for _ in range(20):
        time.sleep(0.8)
        st = get("/api/algo/status")
        if st.get("mode") == "object" and int(st.get("infer_count") or 0) > 0:
            ready = True
            break
        if st.get("object_load_error"):
            break
    fails += not ok("物体模式开启", st.get("mode") == "object")
    if st.get("object_load_error"):
        fails += not ok("物体模型加载", False, st.get("object_load_error"))
    else:
        fails += not ok("物体识别在推理", ready, f"infer={st.get('infer_count')} ms={st.get('last_infer_ms')} dets={st.get('det_count')}")
        print(f"[INFO] 当前检出物体数={st.get('object_count')}（镜头前有杯/瓶/手机/显示器等更容易出框）")
        if st.get("detections"):
            print("[INFO] 示例:", ", ".join(d.get("label", "") for d in st["detections"][:5]))

    post("/api/algo/mode", {"mode": "off"})
    print()
    if fails:
        print(f"结果：有 {fails} 项失败")
        return 1
    print("结果：架构验证通过（摄像头通路 + 人脸/物体 AlgoSink）")
    print("网页上把「算法验证」选成「物体识别」，对着桌上物品即可看橙框。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
