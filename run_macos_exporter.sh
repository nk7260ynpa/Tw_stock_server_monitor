#!/bin/bash
# 啟動 macOS metrics exporter（在主機上直接執行，不透過 Docker）
# 此腳本僅適用於 macOS 環境

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 檢查作業系統
if [[ "$(uname)" != "Darwin" ]]; then
  echo "錯誤：此腳本僅適用於 macOS 環境"
  exit 1
fi

# 安裝依賴
echo "正在檢查 Python 依賴..."
pip3 install -q psutil prometheus_client

# 確保 logs 目錄存在
mkdir -p "${SCRIPT_DIR}/logs"

# 啟動 exporter
echo "正在啟動 macOS Exporter（端口 9101）..."
cd "${SCRIPT_DIR}"
python3 -m src.macos_exporter
