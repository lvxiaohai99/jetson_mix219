#!/bin/bash
# 修复 Jetson 预览黑屏：通常是 nvargus CMA 耗尽 / Argus 卡死
set -euo pipefail

echo "== 当前内存 / CMA =="
free -h | sed -n '1,3p'
grep -E 'CmaTotal|CmaFree|MemAvailable' /proc/meminfo

echo
echo "== 停止 v2l4_learn =="
systemctl --user stop v2l4_learn.service 2>/dev/null || true
pkill -f 'python3 -m backend.app.main' 2>/dev/null || true
sleep 1

echo "== 重启 nvargus-daemon（需要 sudo）=="
sudo systemctl restart nvargus-daemon
sleep 2
systemctl is-active nvargus-daemon

echo "== 重启后 CMA =="
grep -E 'CmaTotal|CmaFree|MemAvailable' /proc/meminfo

echo "== 启动 v2l4_learn =="
systemctl --user start v2l4_learn.service
sleep 3
systemctl --user --no-pager --full status v2l4_learn.service | head -20
curl -s http://127.0.0.1:8080/api/health || true
echo
echo "完成。浏览器刷新预览页。"
