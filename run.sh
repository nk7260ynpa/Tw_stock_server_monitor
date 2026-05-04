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
echo "  - Node Exporter: http://localhost:9100  (Docker VM 指標)"
echo "  (Prometheus 9090 與 Service Monitor 9102 已封閉對外 port，僅供容器內部存取)"

# macOS 環境下自動啟動 macOS Exporter（透過 launchd 管理）
if [[ "$(uname)" == "Darwin" ]]; then
  echo ""
  echo "偵測到 macOS 環境，透過 launchd 啟動 macOS Exporter..."
  bash "${SCRIPT_DIR}/run_macos_exporter.sh"
  echo ""
  echo "提示：Grafana 儀表板頂部可切換 Job 變數："
  echo "  - macos-exporter：macOS 主機真實指標"
  echo "  - node-exporter：Docker VM 指標（Linux 伺服器適用）"
fi
