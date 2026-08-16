"""Docker 容器狀態監控。

透過 Docker Engine API（Unix domain socket）查詢指定容器的執行狀態，
補足 TCP 探測涵蓋不到的「不對外開 port 的常駐容器」。

為什麼需要這一層（2026-07/08 事故）：

`gitlab-runner` 容器曾因 GitLab 本體回 502、空轉後收到 stop signal 而
**正常退出（Exit 0）**，且 restart policy 為 `unless-stopped`
（語義上「被明確 stop 過就不再自動拉起」），結果整整四週沒有任何 CI job
跑得動，卻完全沒有告警。因此對常駐服務而言：

    **容器只要不是 running 就是異常，Exit 0 也不例外。**

該容器不 publish 任何 port，也沒有健康檢查端點，`src.main` 既有的
TCP 探測無法使用，故改以容器狀態（container state）作為訊號來源。
"""

import http.client
import json
import os
import socket
from urllib.parse import quote

from prometheus_client import Gauge

from src.registry import registry
from src.timeutils import parse_iso_timestamp

# 預設設定
DEFAULT_SOCKET_PATH = "/var/run/docker.sock"
DEFAULT_API_VERSION = "v1.41"
DEFAULT_TIMEOUT = 5

# 預設監控的容器（CI 基礎設施）
DEFAULT_CONTAINERS = ("gitlab-runner", "gitlab")

# 容器狀態列舉：固定輸出全部狀態（命中者為 1、其餘為 0），
# 避免用動態 label 導致舊狀態序列殘留而誤觸告警。
CONTAINER_STATES = (
    "running",
    "created",
    "restarting",
    "removing",
    "paused",
    "exited",
    "dead",
    "missing",  # 容器不存在（已被刪除）
    "unknown",  # Docker 回傳了未知狀態值
)

# 容器狀態為 running 的字面值
RUNNING_STATE = "running"


class DockerAPIError(Exception):
    """呼叫 Docker Engine API 失敗時拋出。"""


class _UnixSocketConnection(http.client.HTTPConnection):
    """以 Unix domain socket 連線的 HTTPConnection。

    Docker Engine API 走 `/var/run/docker.sock`，標準函式庫的
    `http.client` 只認得 TCP，故覆寫 `connect()` 改接 AF_UNIX socket。
    如此即可不引入 docker SDK 依賴。
    """

    def __init__(self, socket_path, timeout=DEFAULT_TIMEOUT):
        """初始化連線。

        Args:
            socket_path: Docker socket 路徑。
            timeout: 連線與讀取逾時秒數。
        """
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        """建立 Unix domain socket 連線。"""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


class DockerClient:
    """極簡的 Docker Engine API 唯讀客戶端。"""

    def __init__(self, socket_path=DEFAULT_SOCKET_PATH,
                 api_version=DEFAULT_API_VERSION, timeout=DEFAULT_TIMEOUT):
        """初始化客戶端。

        Args:
            socket_path: Docker socket 路徑。
            api_version: Docker Engine API 版本前綴。
            timeout: 連線與讀取逾時秒數。
        """
        self.socket_path = socket_path
        self.api_version = api_version
        self.timeout = timeout

    def _get(self, path):
        """對 Docker Engine API 發出 GET 請求。

        Args:
            path: 不含版本前綴的 API 路徑（例：`/containers/x/json`）。

        Returns:
            tuple: (status_code, body_bytes)。

        Raises:
            DockerAPIError: 無法連線或連線中斷。
        """
        conn = _UnixSocketConnection(self.socket_path, self.timeout)
        try:
            conn.request("GET", "/{}{}".format(self.api_version, path))
            response = conn.getresponse()
            return response.status, response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DockerAPIError(
                "Docker API 請求失敗（{}）: {}".format(path, exc)
            ) from exc
        finally:
            conn.close()

    def ping(self):
        """檢查 Docker daemon 是否可存取。

        Returns:
            bool: 可存取為 True。

        Raises:
            DockerAPIError: 無法連線。
        """
        status, _ = self._get("/_ping")
        return status == 200

    def inspect_container(self, name):
        """查詢單一容器的詳細狀態。

        Args:
            name: 容器名稱或 ID。

        Returns:
            dict: Docker inspect 結果；容器不存在時回傳 None。

        Raises:
            DockerAPIError: 無法連線或 API 回傳非預期狀態碼。
        """
        path = "/containers/{}/json".format(quote(name, safe=""))
        status, body = self._get(path)
        if status == 404:
            return None
        if status != 200:
            raise DockerAPIError(
                "Docker API 回傳非預期狀態碼 {}（{}）".format(status, name)
            )
        try:
            return json.loads(body)
        except (ValueError, TypeError) as exc:
            raise DockerAPIError(
                "無法解析 Docker API 回應（{}）: {}".format(name, exc)
            ) from exc


# Docker API 是否可存取（1=可存取, 0=不可存取）
docker_api_up = Gauge(
    "tw_stock_docker_api_up",
    "Docker Engine API 是否可存取（1=可存取, 0=不可存取）",
    registry=registry,
)

# 容器是否處於 running 狀態（1=running, 0=其他，含 Exit 0）
container_up = Gauge(
    "tw_stock_container_up",
    "受監控容器是否處於 running 狀態（1=running, 0=其他，含 Exit 0 的 exited）",
    ["container"],
    registry=registry,
)

# 容器目前狀態（命中的 state label 為 1、其餘為 0）
container_state = Gauge(
    "tw_stock_container_state",
    "受監控容器目前狀態（命中的 state 為 1，其餘為 0）",
    ["container", "state"],
    registry=registry,
)

# 容器最後一次結束的 exit code
container_exit_code = Gauge(
    "tw_stock_container_exit_code",
    "受監控容器最後一次結束的 exit code（Exit 0 亦代表非 running）",
    ["container"],
    registry=registry,
)

# 容器啟動時間（Unix epoch 秒）
container_start_timestamp = Gauge(
    "tw_stock_container_start_timestamp_seconds",
    "受監控容器最後一次啟動時間（Unix epoch 秒）",
    ["container"],
    registry=registry,
)

# 容器 restart policy 是否為 always（1=always, 0=其他）
# unless-stopped 被明確 stop 過就不會自動拉起，是本次事故的關鍵之一
container_restart_policy_always = Gauge(
    "tw_stock_container_restart_policy_always",
    "受監控容器的 restart policy 是否為 always（1=always, 0=其他）",
    ["container"],
    registry=registry,
)


def get_monitored_containers(env=None):
    """取得要監控的容器名稱清單。

    可用環境變數 `MONITOR_CONTAINERS`（逗號分隔）覆寫預設值；
    設為空字串代表停用容器監控。

    Args:
        env: 環境變數字典，預設為 `os.environ`（方便測試注入）。

    Returns:
        list: 容器名稱清單。
    """
    source = os.environ if env is None else env
    raw = source.get("MONITOR_CONTAINERS")
    if raw is None:
        return list(DEFAULT_CONTAINERS)
    return [name.strip() for name in raw.split(",") if name.strip()]


def _extract_state(info):
    """從 inspect 結果取出正規化後的容器狀態。

    Args:
        info: Docker inspect 結果，None 代表容器不存在。

    Returns:
        str: `CONTAINER_STATES` 之一。
    """
    if info is None:
        return "missing"
    status = (info.get("State") or {}).get("Status")
    if status in CONTAINER_STATES and status != "missing":
        return status
    return "unknown"


def _update_container_metrics(name, info):
    """更新單一容器的所有指標。

    Args:
        name: 容器名稱。
        info: Docker inspect 結果，None 代表容器不存在。
    """
    state = _extract_state(info)
    container_up.labels(container=name).set(1 if state == RUNNING_STATE else 0)

    for candidate in CONTAINER_STATES:
        container_state.labels(container=name, state=candidate).set(
            1 if candidate == state else 0
        )

    raw_state = (info or {}).get("State") or {}
    exit_code = raw_state.get("ExitCode")
    container_exit_code.labels(container=name).set(
        exit_code if isinstance(exit_code, (int, float)) else 0
    )

    started_at = parse_iso_timestamp(raw_state.get("StartedAt"))
    container_start_timestamp.labels(container=name).set(
        started_at if started_at else 0
    )

    policy = ((info or {}).get("HostConfig") or {}).get("RestartPolicy") or {}
    container_restart_policy_always.labels(container=name).set(
        1 if policy.get("Name") == "always" else 0
    )


def collect_container_health(logger, client, containers):
    """收集所有受監控容器的狀態並更新 Prometheus 指標。

    Docker API 全數查詢失敗時（例如 socket 未掛載），只把
    `tw_stock_docker_api_up` 設為 0 並保留上一輪數值，避免把
    「監控自己壞掉」誤報成「容器全掛」。

    Args:
        logger: Logger 實例。
        client: `DockerClient` 實例。
        containers: 要查詢的容器名稱清單。

    Returns:
        bool: Docker API 是否可存取。
    """
    if not containers:
        docker_api_up.set(1)
        return True

    results = {}
    failures = 0
    for name in containers:
        try:
            results[name] = client.inspect_container(name)
        except DockerAPIError as exc:
            failures += 1
            logger.error("查詢容器 %s 狀態失敗: %s", name, exc)

    if failures == len(containers):
        docker_api_up.set(0)
        logger.error("Docker API 無法存取，容器狀態指標維持上一輪數值")
        return False

    docker_api_up.set(1)
    for name, info in results.items():
        _update_container_metrics(name, info)
        state = _extract_state(info)
        if state == RUNNING_STATE:
            logger.debug("容器 %s 狀態正常（running）", name)
        else:
            exit_code = ((info or {}).get("State") or {}).get("ExitCode")
            logger.warning(
                "容器 %s 未在執行（狀態=%s, exit_code=%s）",
                name, state, exit_code,
            )
    return True
