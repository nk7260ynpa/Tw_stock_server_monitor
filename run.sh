#!/bin/bash
# 啟動主程式的執行腳本
# 透過 Docker container 執行主程式，並掛載 logs 資料夾

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly IMAGE_NAME="tw-stock-server-monitor"
readonly CONTAINER_NAME="tw-stock-server-monitor"

# 確保 logs 目錄存在
mkdir -p "${SCRIPT_DIR}/logs"

# 啟動 Docker container
echo "正在啟動台股伺服器監控..."
docker run --rm \
  --name "${CONTAINER_NAME}" \
  -v "${SCRIPT_DIR}/logs:/app/logs" \
  "${IMAGE_NAME}:latest"
