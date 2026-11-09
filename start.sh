#!/bin/bash
# 启动网页摄像头预览
set -euo pipefail
cd "$(dirname "$0")"

# Argus 服务需要在跑，Jetson CSI 才正常
if ! systemctl is-active --quiet nvargus-daemon; then
  echo "nvargus-daemon 未运行，尝试启动..."
  sudo systemctl start nvargus-daemon
fi

echo "启动后请用浏览器打开："
echo "  局域网:   http://$(hostname -I | awk '{print $1}'):8080"
echo "  Tailscale: http://$(tailscale ip -4 2>/dev/null || echo '100.x.x.x'):8080"
echo
exec python3 -m backend.app.main
