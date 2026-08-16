#!/usr/bin/env bash
#
# 告警推播鏈路端對端演練。
#
# 重現 2026-07/08「gitlab-runner 容器停掉」事故，實測整條
# Prometheus → Alertmanager → 本地推播管道 是否真的會響，並驗證抑制規則
# 讓一次事故只送出一則通知。
#
# 安全性（重要）：
#   * **不會停掉真正的 gitlab-runner**。其他 repo 隨時可能打 tag 部署，
#     停 runner 會讓它們的 pipeline 卡在 pending。事故情境改以假指標端點
#     重現。
#   * 全部跑在獨立的 Docker 網路與獨立容器名稱（alert-drill-*）上，
#     不碰正式的 prometheus / alertmanager / grafana / node-exporter，
#     也不會寫進正式的 Prometheus TSDB。
#   * 使用的是**未經修改的正式 Alertmanager 設定檔**與正式告警規則
#     （僅把 `for:` 與 `interval:` 縮短，好在數十秒內看到結果），
#     所以演練通過等於正式設定通過。
#
# 用法：
#   bash scripts/alert_drill.sh          # 跑完自動清理
#   bash scripts/alert_drill.sh --keep   # 保留容器供人工檢視

set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DRILL_DIR="${ROOT_DIR}/.drill"
readonly NETWORK="alert-drill-net"
readonly IMAGE="nk7260ynpa/tw-stock-monitor:latest"
readonly METRICS_CONTAINER="alert-drill-metrics"
readonly RECEIVER_CONTAINER="alert-drill-receiver"
readonly ALERTMANAGER_CONTAINER="alert-drill-alertmanager"
readonly PROMETHEUS_CONTAINER="alert-drill-prometheus"

# 演練用的縮短時間；正式規則 5m～30m 等不起
readonly DRILL_FOR="10s"
readonly DRILL_INTERVAL="5s"

KEEP_STACK=0

#######################################
# 移除演練容器與網路。
# Globals:
#   NETWORK, *_CONTAINER
#######################################
cleanup() {
  if [[ "${KEEP_STACK}" -eq 1 ]]; then
    echo "== 已保留演練容器（--keep）；手動清理："
    echo "   docker rm -f ${METRICS_CONTAINER} ${RECEIVER_CONTAINER} \
${ALERTMANAGER_CONTAINER} ${PROMETHEUS_CONTAINER}"
    echo "   docker network rm ${NETWORK}"
    return 0
  fi
  docker rm -f "${METRICS_CONTAINER}" "${RECEIVER_CONTAINER}" \
    "${ALERTMANAGER_CONTAINER}" "${PROMETHEUS_CONTAINER}" \
    >/dev/null 2>&1 || true
  docker network rm "${NETWORK}" >/dev/null 2>&1 || true
}

#######################################
# 準備演練用規則：複製正式規則並縮短等待時間。
# Globals:
#   ROOT_DIR, DRILL_DIR, DRILL_FOR, DRILL_INTERVAL
#######################################
prepare_rules() {
  local rules_dir="${DRILL_DIR}/rules"
  rm -rf "${rules_dir}"
  mkdir -p "${rules_dir}"

  local rule_file
  for rule_file in "${ROOT_DIR}"/docker/prometheus/rules/*.yml; do
    sed -E \
      -e "s/^([[:space:]]+)for:[[:space:]]+[0-9]+[smh]$/\1for: ${DRILL_FOR}/" \
      -e "s/^([[:space:]]+)interval:[[:space:]]+[0-9]+[smh]$/\1interval: ${DRILL_INTERVAL}/" \
      "${rule_file}" > "${rules_dir}/$(basename "${rule_file}")"
  done
}

#######################################
# 產生演練用的 Prometheus 設定。
# Globals:
#   DRILL_DIR, DRILL_INTERVAL
#######################################
write_prometheus_config() {
  cat > "${DRILL_DIR}/prometheus.yml" <<EOF
# 演練專用設定（由 scripts/alert_drill.sh 產生，勿手動編輯）
global:
  scrape_interval: ${DRILL_INTERVAL}
  evaluation_interval: ${DRILL_INTERVAL}

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

rule_files:
  - "/etc/prometheus/rules/*.yml"

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]

  # 假指標端點頂替 Service Monitor，重現事故當下的指標樣貌
  - job_name: "service-monitor"
    static_configs:
      - targets: ["fake-metrics:9102"]

  - job_name: "alertmanager"
    static_configs:
      - targets: ["alertmanager:9093"]
EOF
}

#######################################
# 啟動演練堆疊。
# Globals:
#   ROOT_DIR, DRILL_DIR, NETWORK, IMAGE, *_CONTAINER
#######################################
start_stack() {
  docker network create "${NETWORK}" >/dev/null

  # 1. 假指標端點：事故當下的指標快照
  docker run -d --name "${METRICS_CONTAINER}" \
    --network "${NETWORK}" --network-alias fake-metrics \
    -v "${ROOT_DIR}:/app" -w /app "${IMAGE}" \
    python scripts/drill/serve_metrics.py 9102 >/dev/null

  # 2. 接收器：正式的 src/alert_receiver。
  #    網路別名必須是 tw-stock-server-monitor，正式 Alertmanager 設定才
  #    指得到它——這樣才能原封不動地驗證那份設定。
  #    PYTHONPATH 不可省：以檔案路徑執行時 sys.path[0] 是腳本所在目錄
  #    （/app/scripts/drill）而非 /app，`src` 會解析到 image 內較舊的
  #    安裝版本，於是演練驗到的不是工作目錄裡的程式碼。
  docker run -d --name "${RECEIVER_CONTAINER}" \
    --network "${NETWORK}" --network-alias tw-stock-server-monitor \
    -e PYTHONPATH=/app \
    -e ALERT_RECEIVER_PORT=9103 \
    -e ALERT_LOG_DIR=/app/.drill/out \
    -v "${ROOT_DIR}:/app" -w /app "${IMAGE}" \
    python scripts/drill/run_receiver.py >/dev/null

  # 3. Alertmanager：掛載**未經修改**的正式設定
  docker run -d --name "${ALERTMANAGER_CONTAINER}" \
    --network "${NETWORK}" --network-alias alertmanager \
    -v "${ROOT_DIR}/docker/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
    prom/alertmanager:latest \
    --config.file=/etc/alertmanager/alertmanager.yml >/dev/null

  # 4. Prometheus：正式規則（僅縮短等待時間）
  docker run -d --name "${PROMETHEUS_CONTAINER}" \
    --network "${NETWORK}" --network-alias prometheus \
    -v "${DRILL_DIR}/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    -v "${DRILL_DIR}/rules:/etc/prometheus/rules:ro" \
    prom/prometheus:latest \
    --config.file=/etc/prometheus/prometheus.yml >/dev/null
}

#######################################
# 執行驗收腳本。
# Globals:
#   ROOT_DIR, NETWORK, IMAGE
# Returns:
#   驗收腳本的退出碼。
#######################################
run_assertions() {
  docker run --rm --network "${NETWORK}" \
    -e DRILL_NOTIFICATION_DIR=/app/.drill/out \
    -v "${ROOT_DIR}:/app" -w /app "${IMAGE}" \
    python scripts/drill/assert_drill.py
}

#######################################
# 主流程。
# Arguments:
#   命令列參數。
#######################################
main() {
  if [[ "${1:-}" == "--keep" ]]; then
    KEEP_STACK=1
  fi

  if ! docker info >/dev/null 2>&1; then
    echo "錯誤：Docker 未啟動" >&2
    exit 1
  fi

  trap cleanup EXIT
  cleanup

  echo "== 準備演練環境（不會動到正式容器，也不會停 gitlab-runner）=="
  mkdir -p "${DRILL_DIR}/out"
  rm -f "${DRILL_DIR}"/out/notifications-*.jsonl
  prepare_rules
  write_prometheus_config

  echo "== 啟動演練堆疊 =="
  start_stack

  echo "== 等待告警觸發並送達（規則 for 已縮短為 ${DRILL_FOR}）=="
  local status=0
  run_assertions || status=$?

  if [[ "${status}" -ne 0 ]]; then
    echo "== 演練失敗，附上容器日誌 ==" >&2
    docker logs --tail 40 "${RECEIVER_CONTAINER}" >&2 || true
    docker logs --tail 40 "${ALERTMANAGER_CONTAINER}" >&2 || true
    docker logs --tail 40 "${PROMETHEUS_CONTAINER}" >&2 || true
  fi

  return "${status}"
}

main "$@"
