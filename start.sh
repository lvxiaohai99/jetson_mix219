#!/bin/bash
# 启动网页摄像头预览（会先清掉旧进程，避免端口/摄像头被占用）
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8080}"

echo "检查并释放旧实例..."
# 1) 结束本项目旧服务（含其子进程 ffmpeg 录像）
if pgrep -f 'python3 -m backend.app.main' >/dev/null 2>&1; then
  echo "  发现旧的 backend.app.main，正在停止..."
  pkill -f 'python3 -m backend.app.main' 2>/dev/null || true
  sleep 1
  pkill -9 -f 'python3 -m backend.app.main' 2>/dev/null || true
fi
# 2) 再清一次占用端口的进程（防止残留）
if command -v fuser >/dev/null 2>&1; then
  if fuser "${PORT}/tcp" >/dev/null 2>&1; then
    echo "  端口 ${PORT} 仍被占用，正在释放..."
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
    sleep 1
  fi
fi
# 3) 等端口空出来（最多约 5 秒）
for _ in 1 2 3 4 5; do
  if ! fuser "${PORT}/tcp" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if fuser "${PORT}/tcp" >/dev/null 2>&1; then
  echo "错误：端口 ${PORT} 仍被占用，请手动执行：fuser -k ${PORT}/tcp"
  exit 1
fi

# Argus 服务需要在跑，Jetson CSI 才正常
if command -v systemctl >/dev/null 2>&1; then
  if ! systemctl is-active --quiet nvargus-daemon 2>/dev/null; then
    echo "nvargus-daemon 未运行，尝试启动..."
    sudo systemctl start nvargus-daemon
  fi
fi

echo
echo "启动后请用浏览器打开："
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
TS_IP="$(tailscale ip -4 2>/dev/null || true)"
echo "  局域网:    http://${LAN_IP:-<本机IP>}:${PORT}"
echo "  Tailscale: http://${TS_IP:-100.x.x.x}:${PORT}"
echo
exec python3 -m backend.app.main
