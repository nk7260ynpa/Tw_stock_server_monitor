"""Alertmanager webhook 接收器（本地推播管道）。

告警規則寫得再好，沒有推播管道就等於「有人去看才發現」——2026-07/08 的
事故正是四週沒人看。本模組提供一條**不依賴任何外部服務**的預設管道：
Alertmanager 把通知 POST 進來，接收器將其落地為 JSON Lines 檔並轉成
Prometheus 指標。

這條管道有兩個用途：

1. **鏈路可驗證**：不需要 SMTP／Slack 憑證就能證明
   「Prometheus → Alertmanager → 通知」整條路是通的。
2. **死人開關（dead man's switch）**：Alertmanager 會固定把永遠觸發的
   `Watchdog` 告警送到這裡；只要心跳停了，就代表推播鏈路某處斷掉，
   由 `AlertDeliveryStalled` 告警反映。**沉默本身要能被觀測**，這是
   `ServiceMonitorCheckStalled` 同一套思路的延伸。

`tw_stock_alert_watchdog_last_timestamp_seconds` 啟動時即設為行程啟動時間，
而非留在 Gauge 預設值 0：留 0 會讓 `time() - 0` 變成天文數字而永久誤報，
設成啟動時間則「開機後始終收不到心跳」（例如 Alertmanager 設定寫錯）也能
在一個門檻週期內被抓出來。
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import Counter, Gauge

from src.registry import registry

# 預設設定
DEFAULT_PORT = 9103
DEFAULT_LOG_DIR = "logs/alerts"

# 永遠觸發的心跳告警名稱，須與 Alertmanager 路由設定一致
WATCHDOG_ALERTNAME = "Watchdog"

# 請求主體大小上限（位元組），避免異常請求撐爆記憶體
MAX_BODY_BYTES = 1024 * 1024

# 收到的通知數（依告警名稱、嚴重度與 firing/resolved 分類）
notifications_total = Counter(
    "tw_stock_alert_notifications",
    "從 Alertmanager 收到的告警通知數",
    ["alertname", "severity", "status"],
    registry=registry,
)

# 接收器是否正在服務（1=是）
alert_receiver_up = Gauge(
    "tw_stock_alert_receiver_up",
    "Alertmanager webhook 接收器是否正在服務（1=是, 0=否）",
    registry=registry,
)

# 最近一次收到任何通知的時間（Unix epoch 秒）
last_notification_timestamp = Gauge(
    "tw_stock_alert_last_notification_timestamp_seconds",
    "最近一次收到 Alertmanager 通知的時間（Unix epoch 秒）",
    registry=registry,
)

# 最近一次收到 Watchdog 心跳的時間（Unix epoch 秒）
watchdog_timestamp = Gauge(
    "tw_stock_alert_watchdog_last_timestamp_seconds",
    "最近一次收到 Alertmanager Watchdog 心跳的時間（Unix epoch 秒）",
    registry=registry,
)


def _resolve_log_dir():
    """取得通知落地目錄。

    Returns:
        str: 目錄路徑。
    """
    return os.environ.get("ALERT_LOG_DIR", DEFAULT_LOG_DIR)


def _write_notification(record, log_dir):
    """把一則通知附加寫入當日 JSON Lines 檔。

    落地為檔案而非只留在記憶體，是為了在推播管道尚未接上外部通道
    （SMTP／Slack）前仍保有可稽核的通知紀錄。

    Args:
        record: 要寫入的 dict。
        log_dir: 落地目錄。

    Returns:
        str: 實際寫入的檔案路徑；寫入失敗時為 None。
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
        filename = "notifications-{}.jsonl".format(
            time.strftime("%Y%m%d", time.localtime())
        )
        path = os.path.join(log_dir, filename)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except OSError:
        return None


def handle_payload(payload, logger=None, log_dir=None):
    """處理一份 Alertmanager webhook 主體。

    與 HTTP 層拆開，方便單元測試直接驗證解析、指標與落地行為。

    Args:
        payload: Alertmanager 送來的 dict。
        logger: Logger 實例，可為 None。
        log_dir: 通知落地目錄，None 時依環境變數決定。

    Returns:
        int: 本次處理的告警筆數。
    """
    logger = logger or logging.getLogger(__name__)
    log_dir = log_dir or _resolve_log_dir()

    alerts = payload.get("alerts") or []
    now = time.time()
    last_notification_timestamp.set(now)

    handled = 0
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        alertname = labels.get("alertname") or "unknown"
        severity = labels.get("severity") or "unknown"
        # 單筆告警自己的 status 優先，退回整份通知的 status
        status = alert.get("status") or payload.get("status") or "unknown"

        notifications_total.labels(
            alertname=alertname, severity=severity, status=status
        ).inc()
        handled += 1

        if alertname == WATCHDOG_ALERTNAME:
            # 心跳只更新時間戳，不落地也不寫 log，否則每 5 分鐘洗版一次
            watchdog_timestamp.set(now)
            continue

        record = {
            "received_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime(now)
            ),
            "status": status,
            "receiver": payload.get("receiver"),
            "alertname": alertname,
            "severity": severity,
            "component": labels.get("component"),
            "summary": annotations.get("summary"),
            "description": annotations.get("description"),
            "runbook": annotations.get("runbook"),
            "labels": labels,
            "starts_at": alert.get("startsAt"),
            "ends_at": alert.get("endsAt"),
        }
        _write_notification(record, log_dir)

        log = logger.error if severity == "critical" else logger.warning
        if status == "resolved":
            log = logger.info
        log(
            "[告警通知] %s %s（%s）：%s",
            status.upper(), alertname, severity,
            annotations.get("summary") or "",
        )

    return handled


class _WebhookHandler(BaseHTTPRequestHandler):
    """處理 Alertmanager webhook 的 HTTP handler。"""

    # 由 start_alert_receiver 注入
    logger = None
    log_dir = None

    protocol_version = "HTTP/1.1"
    server_version = "TwStockAlertReceiver/1.0"

    def log_message(self, fmt, *args):
        """把 http.server 的預設 stderr 輸出導向專案 logger。

        Args:
            fmt: 格式字串。
            *args: 格式參數。
        """
        if self.logger:
            self.logger.debug("webhook %s", fmt % args)

    def _respond(self, code, body=b""):
        """回覆固定內容。

        Args:
            code: HTTP 狀態碼。
            body: 回應主體（bytes）。
        """
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server 規定的方法名
        """健康檢查端點，供部署後 smoke test 使用。"""
        if self.path.rstrip("/") in ("", "/healthz"):
            self._respond(200, b"ok")
        else:
            self._respond(404, b"not found")

    def do_POST(self):  # noqa: N802 - http.server 規定的方法名
        """接收 Alertmanager 通知。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0

        if length > MAX_BODY_BYTES:
            self._respond(413, b"payload too large")
            return

        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._respond(400, b"invalid json")
            return

        if not isinstance(payload, dict):
            self._respond(400, b"invalid payload")
            return

        try:
            handle_payload(payload, self.logger, self.log_dir)
        except Exception:  # 接收器不可因單筆畸形通知而中斷服務
            if self.logger:
                self.logger.exception("處理告警通知時發生未預期錯誤")
            self._respond(500, b"error")
            return

        self._respond(200, b"ok")


def start_alert_receiver(logger, port=None, log_dir=None):
    """啟動 webhook 接收器（獨立 daemon 執行緒）。

    Args:
        logger: Logger 實例。
        port: 監聽埠，None 時讀 `ALERT_RECEIVER_PORT`。
        log_dir: 通知落地目錄，None 時讀 `ALERT_LOG_DIR`。

    Returns:
        ThreadingHTTPServer: 伺服器實例；啟動失敗時為 None。
    """
    if port is None:
        try:
            port = int(os.environ.get("ALERT_RECEIVER_PORT", DEFAULT_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PORT
    log_dir = log_dir or _resolve_log_dir()

    handler = type(
        "BoundWebhookHandler",
        (_WebhookHandler,),
        {"logger": logger, "log_dir": log_dir},
    )

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    except OSError:
        logger.exception("告警接收器無法在埠 %s 啟動", port)
        alert_receiver_up.set(0)
        return None

    server.daemon_threads = True
    threading.Thread(
        target=server.serve_forever,
        name="alert-receiver",
        daemon=True,
    ).start()

    alert_receiver_up.set(1)
    # 啟動當下就把心跳設為現在，否則 Gauge 預設值 0 會讓
    # AlertDeliveryStalled 在開機瞬間就誤報（同 v1.2.0 踩過的坑）。
    watchdog_timestamp.set(time.time())
    logger.info("告警 webhook 接收器已啟動，監聽埠 %d，通知落地於 %s",
                port, log_dir)
    return server
