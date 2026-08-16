"""台股伺服器監控 - 主程式。

在 Docker 容器中持續運行，並以 Prometheus 格式暴露指標供 Prometheus 抓取。
共有三組 collector：

1. **服務健康檢查**：對各 Tw_stock 微服務做 TCP 探測（`collect_service_health`）。
2. **容器狀態**：查 Docker Engine API 判斷 CI 基礎設施容器是否存活
   （`src.docker_monitor`），涵蓋不對外開 port、TCP 探測不到的容器。
3. **GitLab CI 基礎設施**：查 GitLab API 取得 runner 註冊狀態、pipeline
   失敗原因與 tag 部署落差（`src.gitlab_monitor`）。

第 2、3 組是 2026-07/08「gitlab-runner Exited(0) 四週、零告警」事故後補上的
監控缺口。GitLab API 成本較高，故以獨立且較長的間隔收集。

另外還跑一個 **Alertmanager webhook 接收器**（`src.alert_receiver`），讓告警
真的有地方送達，並以 Watchdog 心跳偵測推播鏈路本身是否斷掉。
"""

import os
import signal
import socket
import threading
import time

from prometheus_client import Gauge, start_http_server

from src.alert_receiver import start_alert_receiver
from src.docker_monitor import (
    DEFAULT_SOCKET_PATH,
    DockerClient,
    collect_container_health,
    get_monitored_containers,
)
from src.gitlab_monitor import (
    DEFAULT_GITLAB_URL,
    DEFAULT_GROUP_ID,
    DEFAULT_INTERVAL as DEFAULT_GITLAB_INTERVAL,
    DEFAULT_JOB_WINDOW_HOURS,
    DEFAULT_TIMEOUT as DEFAULT_GITLAB_TIMEOUT,
    GitLabClient,
    collect_gitlab_ci,
    gitlab_api_up,
    gitlab_token_configured,
    resolve_token,
)
from src.logger import setup_logger
from src.registry import registry

# 預設設定
DEFAULT_PORT = 9102
DEFAULT_CHECK_INTERVAL = 30
DEFAULT_TIMEOUT = 5

# 被監控的服務清單（服務名稱、主機、端口）
# Docker 網路中使用容器名稱作為主機名稱
MONITORED_SERVICES = [
    {"name": "crawler", "host": "tw_stocker_crawler", "port": 6738},
    {"name": "mysql", "host": "tw_stock_database", "port": 3306},
    {"name": "db_operating", "host": "tw_stock_db_operating", "port": 8080},
    {"name": "indicator", "host": "tw-stock-indicator", "port": 5001},
    {"name": "ml", "host": "tw-stock-ml", "port": 5002},
    {"name": "tools", "host": "tw_stock_tools", "port": 8000},
    {"name": "dashboard", "host": "tw_stock_dashboard", "port": 8000},
    {"name": "webpage", "host": "tw-stock-webpage", "port": 8000},
    {"name": "news", "host": "tw_stock_news", "port": 8003},
    {"name": "hot", "host": "tw_stock_hot", "port": 5050},
    {"name": "specialinfo", "host": "tw-stock-specialinfo", "port": 5055},
]

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

# 最近一次完成健康檢查循環的時間（Unix epoch 秒）
# Gauge 不會 stale，主循環卡住時舊值會一直被抓到，必須靠這個指標才看得出
# 「監控自己不動了」——這正是本次事故的失敗模式。
last_check_timestamp = Gauge(
    "tw_stock_last_check_timestamp_seconds",
    "最近一次完成服務與容器健康檢查的時間（Unix epoch 秒）",
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


def _env_int(name, default):
    """讀取整數型環境變數，格式錯誤時退回預設值。

    Args:
        name: 環境變數名稱。
        default: 預設值。

    Returns:
        int: 環境變數值或預設值。
    """
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _interruptible_sleep(seconds):
    """可被終止訊號提早中斷的睡眠。

    以 1 秒為單位分段睡眠，讓 SIGTERM 不必等滿一個檢查間隔才生效。

    Args:
        seconds: 預計睡眠秒數。
    """
    deadline = time.monotonic() + seconds
    while _running:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


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


def build_gitlab_client(logger):
    """依環境變數建立 GitLab API 客戶端。

    未提供權杖時回傳 None（GitLab CI 監控停用），並讓
    `tw_stock_gitlab_token_configured` 維持 0——此狀態本身有對應告警，
    不會變成另一個「靜默失效」。

    Args:
        logger: Logger 實例。

    Returns:
        GitLabClient: 客戶端實例；未設定權杖時為 None。
    """
    token = resolve_token()
    gitlab_token_configured.set(1 if token else 0)
    if not token:
        gitlab_api_up.set(0)
        logger.warning(
            "未設定 GITLAB_TOKEN / GITLAB_TOKEN_FILE，GitLab CI 監控停用"
        )
        return None

    url = os.environ.get("GITLAB_URL", DEFAULT_GITLAB_URL)
    timeout = _env_int("GITLAB_API_TIMEOUT", DEFAULT_GITLAB_TIMEOUT)
    logger.info("GitLab CI 監控已啟用，站台 %s", url)
    return GitLabClient(base_url=url, token=token, timeout=timeout)


def run_gitlab_loop(logger, client, group_id, interval, window_hours):
    """GitLab CI 指標收集迴圈（於獨立執行緒執行）。

    GitLab 端要對群組內每個專案發數次請求，最壞情況（GitLab 無回應且每次
    都等到逾時）可能阻塞數分鐘。若與 TCP 探測共用同一個迴圈，服務健康檢查
    會跟著停擺且毫無徵兆，故拆成獨立執行緒。

    Args:
        logger: Logger 實例。
        client: `GitLabClient` 實例。
        group_id: 要掃描的 GitLab 群組 ID。
        interval: 收集間隔（秒）。
        window_hours: 失敗 job 統計窗口（小時）。
    """
    while _running:
        try:
            collect_gitlab_ci(
                logger, client, group_id, window_hours=window_hours
            )
        except Exception:
            logger.exception("收集 GitLab CI 指標時發生未預期錯誤")
        _interruptible_sleep(interval)


def main():
    """主程式進入點。

    啟動 Prometheus metrics HTTP 伺服器，主循環以固定間隔收集服務健康狀態
    與 CI 基礎設施容器狀態；GitLab CI 指標則由獨立執行緒以較長間隔收集
    （API 較慢，不可拖住主循環）。收到 SIGTERM/SIGINT 時優雅關閉。
    """
    global _running

    logger = setup_logger()
    logger.info("台股伺服器監控啟動")

    # 註冊信號處理器，支援優雅關閉
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 讀取環境變數設定
    port = _env_int("MONITOR_METRICS_PORT", DEFAULT_PORT)
    interval = _env_int("MONITOR_CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL)
    timeout = _env_int("MONITOR_CHECK_TIMEOUT", DEFAULT_TIMEOUT)
    gitlab_interval = _env_int("GITLAB_CHECK_INTERVAL", DEFAULT_GITLAB_INTERVAL)
    gitlab_group = os.environ.get("GITLAB_GROUP_ID", DEFAULT_GROUP_ID)
    gitlab_window = _env_int(
        "GITLAB_JOB_WINDOW_HOURS", DEFAULT_JOB_WINDOW_HOURS
    )

    # 容器狀態監控（CI 基礎設施）
    containers = get_monitored_containers()
    docker_client = DockerClient(
        socket_path=os.environ.get("MONITOR_DOCKER_SOCKET",
                                   DEFAULT_SOCKET_PATH),
        timeout=timeout,
    )

    # GitLab CI 監控
    gitlab_client = build_gitlab_client(logger)

    # 啟動 Prometheus metrics HTTP 伺服器
    start_http_server(port, registry=registry)
    logger.info("Metrics HTTP 伺服器已啟動，監聽端口 %d", port)

    # 啟動 Alertmanager webhook 接收器（本地推播管道 + 心跳死人開關）
    start_alert_receiver(logger)
    logger.info("健康檢查間隔 %d 秒，逾時 %d 秒", interval, timeout)
    logger.info("監控 %d 個服務: %s", len(MONITORED_SERVICES),
                ", ".join(s["name"] for s in MONITORED_SERVICES))
    logger.info("監控 %d 個容器: %s", len(containers),
                ", ".join(containers) if containers else "（停用）")
    logger.info("GitLab CI 指標收集間隔 %d 秒（群組 %s）",
                gitlab_interval, gitlab_group)

    # GitLab 收集另起執行緒，避免其逾時拖住服務健康檢查
    if gitlab_client is not None:
        threading.Thread(
            target=run_gitlab_loop,
            args=(logger, gitlab_client, gitlab_group, gitlab_interval,
                  gitlab_window),
            name="gitlab-collector",
            daemon=True,
        ).start()

    # 主循環：定期收集服務與容器指標
    while _running:
        try:
            collect_service_health(logger, timeout)
        except Exception:
            logger.exception("收集服務健康狀態時發生未預期錯誤")

        try:
            collect_container_health(logger, docker_client, containers)
        except Exception:
            logger.exception("收集容器狀態時發生未預期錯誤")

        last_check_timestamp.set(time.time())
        _interruptible_sleep(interval)

    logger.info("台股伺服器監控結束")


if __name__ == "__main__":
    main()
