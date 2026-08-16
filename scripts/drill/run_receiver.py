"""告警演練用的接收器行程。

直接啟動正式的 `src.alert_receiver`（不是模擬品），確保演練驗到的是真的
會上線的那份程式碼；另外開一個 `/metrics` 端點，讓驗收腳本讀得到
Watchdog 心跳次數。

用法：
    python run_receiver.py
"""

import logging
import sys
import time

from prometheus_client import start_http_server

from src.alert_receiver import start_alert_receiver
from src.registry import registry

# 演練專用的指標埠，與正式的 9102 分開，避免誤會成正式服務
METRICS_PORT = 9199


def main():
    """啟動接收器並常駐。

    Returns:
        int: 退出碼。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("alert-drill-receiver")

    start_http_server(METRICS_PORT, registry=registry)
    if start_alert_receiver(logger) is None:
        logger.error("接收器啟動失敗")
        return 1

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
