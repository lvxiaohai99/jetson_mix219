#!/bin/bash
# 启用 IMX219 CSI 摄像头启动项，确认 Tailscale 开机自启，然后重启
set -euo pipefail

CONF=/boot/extlinux/extlinux.conf
BACKUP=/boot/extlinux/extlinux.conf.bak.$(date +%Y%m%d_%H%M%S)

if [[ $EUID -ne 0 ]]; then
  echo "请用: sudo bash $0"
  exit 1
fi

echo "==== 1) 备份并修改启动项 ===="
cp -a "$CONF" "$BACKUP"
echo "已备份到: $BACKUP"

# 只把 DEFAULT primary 改成 DEFAULT JetsonIO（若已是 JetsonIO 则跳过）
if grep -q '^DEFAULT JetsonIO' "$CONF"; then
  echo "DEFAULT 已经是 JetsonIO，无需再改"
elif grep -q '^DEFAULT primary' "$CONF"; then
  sed -i 's/^DEFAULT primary/DEFAULT JetsonIO/' "$CONF"
  echo "已将 DEFAULT primary -> DEFAULT JetsonIO"
else
  echo "未找到 DEFAULT primary/JetsonIO，请人工检查 $CONF"
  exit 1
fi

echo
echo "==== 2) 确认修改结果 ===="
grep -E 'DEFAULT|LABEL JetsonIO|OVERLAYS|MENU LABEL Custom' "$CONF"
if ! grep -q '^DEFAULT JetsonIO' "$CONF"; then
  echo "错误: DEFAULT 不是 JetsonIO，中止重启"
  exit 1
fi
if ! grep -A6 '^LABEL JetsonIO' "$CONF" | grep -q 'imx219'; then
  echo "错误: JetsonIO 项里没有 imx219 overlay，中止重启"
  exit 1
fi
echo "启动配置确认 OK"

echo
echo "==== 3) Tailscale 开机自启 ===="
systemctl enable tailscaled
systemctl is-enabled tailscaled
systemctl is-active tailscaled || systemctl start tailscaled
echo "Tailscale: enabled + $(systemctl is-active tailscaled)"

echo
echo "==== 4) 5 秒后重启（可用 Ctrl+C 取消）===="
sleep 5
reboot
