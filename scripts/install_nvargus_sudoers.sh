#!/bin/bash
# 一次性：允许用户 lht 免密重启 nvargus（网页恢复按钮需要）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESTART="$SCRIPT_DIR/restart_nvargus.sh"
USER_NAME="${SUDO_USER:-${USER:-lht}}"
TARGET="/etc/sudoers.d/v2l4-nvargus"

chmod +x "$RESTART"
TMP="$(mktemp)"
cat >"$TMP" <<EOF
# v2l4_learn: allow web recover button to restart Argus without password
${USER_NAME} ALL=(root) NOPASSWD: ${RESTART}, /bin/systemctl restart nvargus-daemon, /bin/systemctl restart nvargus-daemon.service
EOF

echo "将写入 $TARGET :"
cat "$TMP"
echo
sudo install -m 440 "$TMP" "$TARGET"
rm -f "$TMP"
sudo visudo -cf "$TARGET"
echo
echo "测试免密重启..."
sudo -n "$RESTART" && echo "OK：网页「恢复摄像头」已可用"
