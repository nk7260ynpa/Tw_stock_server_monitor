#!/bin/bash
# 建立 Docker image 的執行腳本

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
readonly IMAGE_NAME="tw-stock-server-monitor"
readonly IMAGE_TAG="latest"

echo "正在建立 Docker image: ${IMAGE_NAME}:${IMAGE_TAG}"
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f "${SCRIPT_DIR}/Dockerfile" "${PROJECT_DIR}"
echo "Docker image 建立完成: ${IMAGE_NAME}:${IMAGE_TAG}"
