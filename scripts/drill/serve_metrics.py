"""告警演練用的假指標端點。

模擬「gitlab-runner 容器 Exited(0)」事故發生當下 Service Monitor 會輸出的
指標：runner 容器掛掉，連帶 runner 離線、job 卡住、pipeline 失敗、tag 沒
部署全部成立。用途是讓演練走**真實的規則評估**，而不是直接對 Alertmanager
塞假告警——這樣才驗得到 expr 本身。

刻意**不**去停真的 runner 容器：其他 repo 隨時可能在打 tag 部署，停 runner
會讓它們的 pipeline 卡住。

用法：
    python serve_metrics.py [port]
"""

import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# 事故當下的指標快照。
# 上半部是「健康」的前提條件——必須齊備，否則 ServiceMonitorMetricsMissing
# 之類的上游告警會先觸發並把整批下游告警抑制掉，演練就驗不到想驗的東西。
METRICS_TEMPLATE = """\
# HELP tw_stock_service_up 服務健康狀態
# TYPE tw_stock_service_up gauge
tw_stock_service_up{{service="drill",host="drill",port="1"}} 1
# HELP tw_stock_last_check_timestamp_seconds 最近一次完成健康檢查的時間
# TYPE tw_stock_last_check_timestamp_seconds gauge
tw_stock_last_check_timestamp_seconds {now}
# HELP tw_stock_docker_api_up Docker API 是否可用
# TYPE tw_stock_docker_api_up gauge
tw_stock_docker_api_up 1
# HELP tw_stock_alert_receiver_up 告警接收器是否服務中
# TYPE tw_stock_alert_receiver_up gauge
tw_stock_alert_receiver_up 1
# HELP tw_stock_alert_watchdog_last_timestamp_seconds 最近一次心跳
# TYPE tw_stock_alert_watchdog_last_timestamp_seconds gauge
tw_stock_alert_watchdog_last_timestamp_seconds {now}
# HELP tw_stock_gitlab_token_configured 是否已設定權杖
# TYPE tw_stock_gitlab_token_configured gauge
tw_stock_gitlab_token_configured 1
# HELP tw_stock_gitlab_api_up GitLab API 是否可用
# TYPE tw_stock_gitlab_api_up gauge
tw_stock_gitlab_api_up 1
# HELP tw_stock_gitlab_last_collect_timestamp_seconds 最近一次收集時間
# TYPE tw_stock_gitlab_last_collect_timestamp_seconds gauge
tw_stock_gitlab_last_collect_timestamp_seconds {now}

# ── 以下為事故本體：runner 容器 Exit 0，及其必然的連鎖後果 ──────────
# HELP tw_stock_container_up 容器是否在執行
# TYPE tw_stock_container_up gauge
tw_stock_container_up{{container="gitlab-runner"}} 0
tw_stock_container_up{{container="gitlab"}} 1
tw_stock_container_up{{container="alertmanager"}} 1
# HELP tw_stock_gitlab_runners_online_total online 的 runner 數
# TYPE tw_stock_gitlab_runners_online_total gauge
tw_stock_gitlab_runners_online_total 0
# HELP tw_stock_gitlab_runner_online 個別 runner 是否 online
# TYPE tw_stock_gitlab_runner_online gauge
tw_stock_gitlab_runner_online{{runner_id="1",description="drill-runner"}} 0
# HELP tw_stock_gitlab_runner_last_contact_seconds runner 最後聯繫距今秒數
# TYPE tw_stock_gitlab_runner_last_contact_seconds gauge
tw_stock_gitlab_runner_last_contact_seconds{{runner_id="1",\
description="drill-runner"}} 999999
# HELP tw_stock_gitlab_failed_jobs 失敗 job 數
# TYPE tw_stock_gitlab_failed_jobs gauge
tw_stock_gitlab_failed_jobs{{project="Drill_Project",\
failure_reason="stuck_pending_no_matching_runners"}} 4
# HELP tw_stock_gitlab_pipeline_status 最新 pipeline 狀態
# TYPE tw_stock_gitlab_pipeline_status gauge
tw_stock_gitlab_pipeline_status{{project="Drill_Project",status="failed"}} 1
# HELP tw_stock_gitlab_tag_undeployed_seconds 最新 tag 未部署秒數
# TYPE tw_stock_gitlab_tag_undeployed_seconds gauge
tw_stock_gitlab_tag_undeployed_seconds{{project="Drill_Project",\
tag="v9.9.9"}} 7200
# HELP tw_stock_gitlab_tag_pipeline_status tag 的部署狀態
# TYPE tw_stock_gitlab_tag_pipeline_status gauge
tw_stock_gitlab_tag_pipeline_status{{project="Drill_Project",tag="v9.9.9",\
status="missing"}} 1
"""


class _MetricsHandler(BaseHTTPRequestHandler):
    """回傳固定指標內容的 handler。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        """關閉預設的 stderr 存取日誌。

        Args:
            fmt: 格式字串。
            *args: 格式參數。
        """

    def do_GET(self):  # noqa: N802 - http.server 規定的方法名
        """回應 /metrics 抓取請求。"""
        body = METRICS_TEMPLATE.format(now=int(time.time())).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    """啟動假指標 HTTP 伺服器。"""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9102
    HTTPServer(("0.0.0.0", port), _MetricsHandler).serve_forever()


if __name__ == "__main__":
    main()
