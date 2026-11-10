#!/bin/bash
# 仅重启 Jetson Argus 摄像头守护进程（供网页「恢复摄像头」调用）
# 需配合 scripts/install_nvargus_sudoers.sh 配置免密 sudo
set -euo pipefail
exec /bin/systemctl restart nvargus-daemon.service
