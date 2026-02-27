"""台股伺服器監控 - 主程式。

在 Docker 容器中持續運行，定期檢查各 Tw_stock 服務的健康狀態，
並以 Prometheus 格式暴露指標供 Prometheus 抓取。
"""

import logging
import os
import signal
import socket
import sys
import time

from prometheus_client import CollectorRegistry, Gauge, start_http_server

from src.logger import setup_logger

# 預設設定
DEFAULT_PORT = 9102
DEFAULT_CHECK_INTERVAL = 30
DEFAULT_TIMEOUT = 5

# 被監控的服務清單（服務名稱、主機、端口）
# Docker 網路中使用容器名稱作為主機名稱
MONITORED_SERVICES = [
    {"name": "crawler", "host": "tw_stock_crawler", "port": 6738},
    {"name": "mysql", "host": "tw_stock_database", "port": 3306},
    {"name": "db_operating", "host": "tw_stock_db_operating", "port": 8080},
    {"name": "indicator", "host": "tw-stock-indicator", "port": 5001},
    {"name": "tools", "host": "tw_stock_tools", "port": 8000},
    {"name": "dashboard", "host": "tw_stock_dashboard", "port": 8002},
    {"name": "webpage", "host": "tw-stock-webpage", "port": 7938},
    {"name": "news", "host": "tw_stock_news", "port": 8003},
]

# 建立獨立的 registry
registry = CollectorRegistry()

# 服務健康狀態指標（1=正常, 0=異常）
service_up = Gauge(
    "tw_stock_service_up",
    "Tw_stock 服務健康狀態（1=正常, 0=異常）",
    ["service", "host", "port"],
    registry=registry,
)

# 服務回應時間指標（秒）
service_response_time = Gauge(
    "tw_stock_service_response_time_seconds",
    "Tw_stock 服務 TCP 連線回應時間（秒）",
    ["service", "host", "port"],
    registry=registry,
)

# 控制主循環的旗標
_running = True


def _signal_handler(signum, frame):
    """處理終止訊號，優雅關閉程式。

    Args:
        signum: 信號編號。
        frame: 當前堆疊框架。
    """
    global _running
    _running = False


def check_service(host, port, timeout=DEFAULT_TIMEOUT):
    """檢查指定服務的 TCP 連線是否可用。

    透過嘗試建立 TCP 連線來判斷服務是否正在運行。

    Args:
        host: 服務主機名稱或 IP 位址。
        port: 服務端口號。
        timeout: 連線逾時秒數。

    Returns:
        tuple: (is_up, response_time)
            is_up (bool): 服務是否可用。
            response_time (float): 連線回應時間（秒），失敗時為 -1。
    """
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        elapsed = time.monotonic() - start
        sock.close()
        return True, elapsed
    except (socket.timeout, socket.error, OSError):
        return False, -1


def collect_service_health(logger, timeout=DEFAULT_TIMEOUT):
    """收集所有被監控服務的健康狀態。

    逐一檢查 MONITORED_SERVICES 中的服務，更新 Prometheus 指標。

    Args:
        logger: Logger 實例。
        timeout: 每個服務的連線逾時秒數。
    """
    for svc in MONITORED_SERVICES:
        name = svc["name"]
        host = svc["host"]
        port = svc["port"]

        is_up, response_time = check_service(host, port, timeout)

        port_str = str(port)
        service_up.labels(service=name, host=host, port=port_str).set(
            1 if is_up else 0
        )

        if is_up:
            service_response_time.labels(
                service=name, host=host, port=port_str
            ).set(response_time)
            logger.debug(
                "服務 %s (%s:%d) 正常，回應時間 %.3fs",
                name, host, port, response_time,
            )
        else:
            service_response_time.labels(
                service=name, host=host, port=port_str
            ).set(-1)
            logger.warning("服務 %s (%s:%d) 無法連線", name, host, port)


def main():
    """主程式進入點。

    啟動 Prometheus metrics HTTP 伺服器，並以固定間隔持續檢查
    各 Tw_stock 服務的健康狀態。收到 SIGTERM/SIGINT 時優雅關閉。
    """
    global _running

    logger = setup_logger()
    logger.info("台股伺服器監控啟動")

    # 註冊信號處理器，支援優雅關閉
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 讀取環境變數設定
    port = int(os.environ.get("MONITOR_METRICS_PORT", DEFAULT_PORT))
    interval = int(os.environ.get("MONITOR_CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL))
    timeout = int(os.environ.get("MONITOR_CHECK_TIMEOUT", DEFAULT_TIMEOUT))

    # 啟動 Prometheus metrics HTTP 伺服器
    start_http_server(port, registry=registry)
    logger.info("Metrics HTTP 伺服器已啟動，監聽端口 %d", port)
    logger.info("健康檢查間隔 %d 秒，逾時 %d 秒", interval, timeout)
    logger.info("監控 %d 個服務: %s", len(MONITORED_SERVICES),
                ", ".join(s["name"] for s in MONITORED_SERVICES))

    # 主循環：定期收集服務健康狀態
    while _running:
        try:
            collect_service_health(logger, timeout)
        except Exception:
            logger.exception("收集服務健康狀態時發生未預期錯誤")
        time.sleep(interval)

    logger.info("台股伺服器監控結束")


if __name__ == "__main__":
    main()
