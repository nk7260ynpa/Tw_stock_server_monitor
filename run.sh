#!/bin/bash
# 啟動主程式的執行腳本
# 透過 Docker Compose 啟動所有服務（含監控系統），並掛載 logs 資料夾
# 在 macOS 環境下，會額外啟動原生的 macOS Exporter

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 確保 logs 目錄存在
mkdir -p "${SCRIPT_DIR}/logs"

# 啟動所有 Docker 服務
echo "正在啟動台股伺服器監控與監控系統..."
docker compose -f "${SCRIPT_DIR}/docker/docker-compose.yaml" up -d

echo ""
echo "服務已啟動："
echo "  - Grafana:       http://localhost:3000  (帳號: admin / 密碼: admin)"
echo "  - Prometheus:    http://localhost:9090"
echo "  - Node Exporter: http://localhost:9100  (Docker VM 指標)"

# macOS 環境下自動啟動 macOS Exporter
if [[ "$(uname)" == "Darwin" ]]; then
  echo ""
  echo "偵測到 macOS 環境，啟動 macOS Exporter..."
  pip3 install -q psutil prometheus_client

  # 停止已存在的 macOS Exporter
  if pgrep -f "src.macos_exporter" > /dev/null 2>&1; then
    echo "  停止舊的 macOS Exporter..."
    pkill -f "src.macos_exporter" || true
    sleep 1
  fi

  cd "${SCRIPT_DIR}"
  nohup python3 -m src.macos_exporter > "${SCRIPT_DIR}/logs/macos_exporter.log" 2>&1 &
  echo "  - macOS Exporter: http://localhost:9101  (macOS 主機指標)"
  echo ""
  echo "提示：Grafana 儀表板頂部可切換 Job 變數："
  echo "  - macos-exporter：macOS 主機真實指標"
  echo "  - node-exporter：Docker VM 指標（Linux 伺服器適用）"
fi
