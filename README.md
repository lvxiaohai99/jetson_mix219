# v2l4_learn

Jetson Orin Nano 排线摄像头（IMX219）学习项目。

**前后端分离**：后端只做视频源与 API，前端负责预览 / 控制 / 日历。

## 文档

| 文档 | 内容 |
|------|------|
| [doc/架构设计.md](doc/架构设计.md) | 整体架构、目录、API、启动注意 |
| [doc/算法与模型.md](doc/算法与模型.md) | **算法原理 + 模型下载/部署/切换**（重点） |

## 功能

- 唯一视频源（Argus）+ FrameHub 分发
- 预览：`mjpeg_hw` / `h264_soft`，帧率 5~30 可配
- 旋转 / 缩放 / 锁定设置 / 去红边 / 时间显示
- 截图、H.264 MP4 录制（软件编码；Orin Nano **无** H.264 硬编）
- 左侧媒体日历：绿框有文件、灰框无文件；可删媒体
- 自动录制、滚存天数、预计占用
- 算法验证（AlgoSink）：人脸 / 物体识别，预览画框，**不另开摄像头**

## 快速开始

```bash
cd /home/lht/sync/v2l4_learn
pip3 install -r requirements.txt
# 若 pip 装上了 opencv-python，务必卸掉，否则 CSI 摄像头会坏：
# pip3 uninstall -y opencv-python opencv-python-headless
./start.sh
```

`./start.sh` 会先停掉旧服务并释放 **8080** 与摄像头，再启动。

浏览器打开：`http://<IP>:8080`

### 物体识别怎么开

1. 按 [doc/算法与模型.md](doc/算法与模型.md) 把权重放到 `models/`（至少 `yolov8n.pt`）  
2. 网页「算法验证」选 **物体识别**，「识别模型」选 **YOLOv8n** 或自动  
3. 无人时也可验证：`python3 scripts/verify_algo.py`

常用下载（放到 `models/`）：

| 文件 | 地址 |
|------|------|
| `yolov8n.pt` | https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt |
| `fasterrcnn_mobilenet_v3_large_320_fpn.pth` | https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth |
| `ssdlite320_mobilenet_v3_large_coco.pth` | https://download.pytorch.org/models/ssdlite320_mobilenet_v3_large_coco-a79551df.pth |

## 目录

```text
backend/app/     # API、视频源、Hub、Sink、媒体索引
frontend/        # 静态页面与 JS/CSS
models/          # 算法权重（需自行下载）
doc/             # 架构 + 算法模型说明
scripts/         # 验证脚本
start.sh         # 推荐启动方式
settings.json    # 本地持久设置
```

## 常用设置（预览页）

| 项 | 说明 |
|---|---|
| 预览帧数 | 5~30，改后摄像头会短暂重开 |
| 默认自动录制 | 开着时 ffmpeg 会持续占 CPU（软件 H.264） |
| 滚存天数 | 过期截图/录像自动删 |
| 算法验证 | 关闭 / 人脸 / 物体 |
| 识别模型 | 自动 / YOLOv8n / FasterRCNN / SSDLite |

## 录制说明

- 约 **5~12 MB/分钟**（估算按 8MB/分钟）
- 录制中：`*.part.mp4` + 延迟预览 `*.preview.mp4`；停止后成标准 `*.mp4`
- 关掉物体识别 **不会** 停录像；要停请点「停止录制」或取消自动录制

## 已知注意

- 改帧率会重开 Argus，画面闪一下
- Orin Nano 不支持 H.264 硬编码，长时间录像 CPU 偏高属正常
- 滚存约每 10 分钟清理；改天数会立刻清一次
- 回放页暂不完善
