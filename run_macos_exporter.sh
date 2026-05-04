#!/bin/bash
# 啟動 macOS metrics exporter（使用 launchd 管理，開機自啟、當機自重啟）
# 此腳本僅適用於 macOS 環境
#
# 用法：
#   bash run_macos_exporter.sh          啟動/重啟服務
#   bash run_macos_exporter.sh stop     停止服務並移除開機自啟
#   bash run_macos_exporter.sh status   查看服務狀態

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PLIST_LABEL="com.twstock.macos-exporter"
readonly PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
readonly LOG_DIR="${SCRIPT_DIR}/logs"

# 檢查作業系統
if [[ "$(uname)" != "Darwin" ]]; then
  echo "錯誤：此腳本僅適用於 macOS 環境"
  exit 1
fi

_stop_service() {
  if launchctl list "${PLIST_LABEL}" &>/dev/null; then
    launchctl unload "${PLIST_PATH}" 2>/dev/null || true
  fi
  # 清理可能殘留的舊 nohup 進程
  if pgrep -f "src.macos_exporter" >/dev/null 2>&1; then
    pkill -f "src.macos_exporter" || true
    sleep 1
  fi
}

_status() {
  if launchctl list "${PLIST_LABEL}" &>/dev/null; then
    echo "macOS Exporter 服務狀態：運行中"
    launchctl list "${PLIST_LABEL}"
  else
    echo "macOS Exporter 服務狀態：未運行"
  fi
}

_start_service() {
  # 安裝依賴
  echo "正在檢查 Python 依賴..."
  pip3 install -q psutil prometheus_client

  mkdir -p "${LOG_DIR}"
  mkdir -p "${HOME}/Library/LaunchAgents"

  # 停止舊服務
  _stop_service

  # 取得 python3 絕對路徑
  local python3_path
  python3_path="$(which python3)"

  # 生成 LaunchAgent plist
  cat > "${PLIST_PATH}" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${python3_path}</string>
        <string>-m</string>
        <string>src.macos_exporter</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/macos_exporter.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/macos_exporter_error.log</string>
</dict>
</plist>
EOF

  launchctl load "${PLIST_PATH}"

  echo "macOS Exporter 已透過 launchd 啟動（端口 9101）"
  echo "  服務標籤：${PLIST_LABEL}"
  echo "  開機自動啟動：是"
  echo "  當機自動重啟：是"
  echo "  日誌：${LOG_DIR}/macos_exporter.log"
  echo ""
  echo "管理指令："
  echo "  停止服務：bash $0 stop"
  echo "  查看狀態：bash $0 status"
}

case "${1:-start}" in
  stop)
    echo "正在停止 macOS Exporter 服務..."
    _stop_service
    rm -f "${PLIST_PATH}"
    echo "macOS Exporter 服務已停止並移除開機自啟"
    ;;
  status)
    _status
    ;;
  start|*)
    _start_service
    ;;
esac
