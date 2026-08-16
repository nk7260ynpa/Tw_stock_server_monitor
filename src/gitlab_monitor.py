"""GitLab CI 基礎設施監控。

以 GitLab REST API v4 蒐集「CI 到底跑不跑得動」的狀態，並暴露為
Prometheus 指標。涵蓋四件事：

1. **Runner 註冊狀態**：GitLab 認定 runner online / offline、最後聯繫時間。
   比容器狀態更貼近「job 到底有沒有人接」。
2. **Pipeline 失敗**：各專案最新一條 pipeline 的狀態。
3. **失敗原因分類**：近期失敗 job 依 `failure_reason` 分類，可區分
   `stuck_pending_no_matching_runners`（沒有 runner 可接＝基礎設施問題）
   與 `script_failure`（程式碼問題）。
4. **tag 與部署脫節**：最新 `vX.Y.Z` tag 是否真的有一條成功的 pipeline。
   2026-07/08 那次事故最直接的業務影響就是「tag 打了但沒部署」。

認證：需要具 `read_api` scope 的 token，由環境變數 `GITLAB_TOKEN`
或 `GITLAB_TOKEN_FILE`（token 檔案路徑）提供。**權杖絕不寫進程式碼、
也不寫入日誌**；未設定時本模組會停用並讓 `tw_stock_gitlab_token_configured`
維持 0（此狀態本身即有對應告警，不會靜默失效）。
"""

import os
import time

import requests
from prometheus_client import Gauge

from src.registry import registry
from src.timeutils import parse_iso_timestamp

# 預設設定
# 容器內必須用 host.docker.internal 才連得到 host 上的自架 GitLab
# （127.0.0.1 在容器內指的是容器自己，是踩過的坑）。
DEFAULT_GITLAB_URL = "http://host.docker.internal:8080"
DEFAULT_GROUP_ID = "38"          # twstock 群組
DEFAULT_INTERVAL = 300           # GitLab API 收集間隔（秒）
DEFAULT_TIMEOUT = 10             # 單次 API 請求逾時（秒）
DEFAULT_JOB_WINDOW_HOURS = 24    # 失敗 job 統計窗口（小時）
DEFAULT_JOB_PAGE_SIZE = 30       # 每個專案抓取的最近失敗 job 數上限

# 沒有 runner 可接 job 的失敗原因（基礎設施問題，非程式碼問題）
STUCK_FAILURE_REASON = "stuck_pending_no_matching_runners"

# pipeline 不存在時使用的合成狀態值
PIPELINE_STATUS_MISSING = "missing"


class GitLabAPIError(Exception):
    """呼叫 GitLab API 失敗時拋出。"""


def resolve_token(env=None):
    """取得 GitLab API 權杖。

    優先使用 `GITLAB_TOKEN`；未設定時改讀 `GITLAB_TOKEN_FILE` 指向的檔案
    （適合以唯讀掛載方式提供密鑰，避免權杖進入 CI/CD 變數或 shell 歷史）。

    Args:
        env: 環境變數字典，預設為 `os.environ`（方便測試注入）。

    Returns:
        str: 權杖字串；未設定或檔案讀取失敗時回傳空字串。
    """
    source = os.environ if env is None else env

    token = (source.get("GITLAB_TOKEN") or "").strip()
    if token:
        return token

    token_path = (source.get("GITLAB_TOKEN_FILE") or "").strip()
    if not token_path:
        return ""
    try:
        with open(token_path, encoding="utf-8") as token_file:
            return token_file.read().strip()
    except OSError:
        return ""


class GitLabClient:
    """GitLab REST API v4 唯讀客戶端。"""

    def __init__(self, base_url=DEFAULT_GITLAB_URL, token="",
                 timeout=DEFAULT_TIMEOUT):
        """初始化客戶端。

        Args:
            base_url: GitLab 站台網址（不含 `/api/v4`）。
            token: 具 `read_api` scope 的權杖。
            timeout: 單次請求逾時秒數。
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if token:
            self.session.headers.update({"PRIVATE-TOKEN": token})

    def get(self, path, params=None):
        """對 GitLab API 發出 GET 請求並回傳解析後的 JSON。

        Args:
            path: 相對於 `/api/v4` 的路徑（例：`/groups/38/runners`）。
            params: query string 參數字典。

        Returns:
            解析後的 JSON（dict 或 list）。

        Raises:
            GitLabAPIError: 連線失敗、非 2xx 狀態碼或回應無法解析。
        """
        url = "{}/api/v4{}".format(self.base_url, path)
        try:
            response = self.session.get(
                url, params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise GitLabAPIError(
                "GitLab API 連線失敗（{}）: {}".format(path, exc)
            ) from exc

        if response.status_code >= 400:
            raise GitLabAPIError(
                "GitLab API 回傳狀態碼 {}（{}）".format(
                    response.status_code, path
                )
            )
        try:
            return response.json()
        except ValueError as exc:
            raise GitLabAPIError(
                "無法解析 GitLab API 回應（{}）: {}".format(path, exc)
            ) from exc


# ── 指標定義 ──────────────────────────────────────────────────────────

# GitLab API 是否可存取（1=可存取, 0=不可存取）
gitlab_api_up = Gauge(
    "tw_stock_gitlab_api_up",
    "GitLab API 是否可存取（1=可存取, 0=不可存取）",
    registry=registry,
)

# 是否已設定 GitLab API 權杖（1=已設定, 0=未設定）
gitlab_token_configured = Gauge(
    "tw_stock_gitlab_token_configured",
    "是否已提供 GitLab API 權杖（1=已設定, 0=未設定，未設定時 CI 監控停用）",
    registry=registry,
)

# Runner 是否 online（1=online, 0=offline）
gitlab_runner_online = Gauge(
    "tw_stock_gitlab_runner_online",
    "GitLab 認定該 runner 是否 online（1=online, 0=offline）",
    ["runner_id", "description"],
    registry=registry,
)

# Runner 是否被暫停（1=paused, 0=啟用中）
gitlab_runner_paused = Gauge(
    "tw_stock_gitlab_runner_paused",
    "GitLab runner 是否被暫停（1=paused, 0=啟用中）",
    ["runner_id", "description"],
    registry=registry,
)

# Runner 最後一次與 GitLab 聯繫至今的秒數
gitlab_runner_last_contact_seconds = Gauge(
    "tw_stock_gitlab_runner_last_contact_seconds",
    "GitLab runner 最後一次聯繫至今的秒數（越大代表越久沒 polling）",
    ["runner_id", "description"],
    registry=registry,
)

# 目前 online 的 runner 數量
gitlab_runners_online_total = Gauge(
    "tw_stock_gitlab_runners_online_total",
    "目前 online 的 GitLab runner 數量（0 代表所有 job 都跑不動）",
    registry=registry,
)

# 已註冊的 runner 總數
gitlab_runners_total = Gauge(
    "tw_stock_gitlab_runners_total",
    "群組可用的 GitLab runner 註冊總數",
    registry=registry,
)

# 各專案最新一條 pipeline 的狀態（命中的 status 為 1）
gitlab_pipeline_status = Gauge(
    "tw_stock_gitlab_pipeline_status",
    "各專案最新一條 pipeline 的狀態（值恆為 1，狀態見 status label）",
    ["project", "ref", "status"],
    registry=registry,
)

# 各專案最新一條 pipeline 的建立時間（Unix epoch 秒）
gitlab_pipeline_timestamp_seconds = Gauge(
    "tw_stock_gitlab_pipeline_timestamp_seconds",
    "各專案最新一條 pipeline 的建立時間（Unix epoch 秒）",
    ["project"],
    registry=registry,
)

# 統計窗口內失敗 job 數，依 failure_reason 分類
gitlab_failed_jobs = Gauge(
    "tw_stock_gitlab_failed_jobs",
    "統計窗口內的失敗 job 數，依 failure_reason 分類"
    "（stuck_pending_no_matching_runners 代表沒有 runner 可接，非程式碼問題）",
    ["project", "failure_reason"],
    registry=registry,
)

# 各專案最新版本 tag 對應 pipeline 的狀態
gitlab_tag_pipeline_status = Gauge(
    "tw_stock_gitlab_tag_pipeline_status",
    "各專案最新 tag 對應 pipeline 的狀態（值恆為 1，missing 代表該 tag 沒有 pipeline）",
    ["project", "tag", "status"],
    registry=registry,
)

# 最新 tag 尚未成功部署的秒數（已成功則為 0）
gitlab_tag_undeployed_seconds = Gauge(
    "tw_stock_gitlab_tag_undeployed_seconds",
    "最新 tag 距今尚未成功部署的秒數（該 tag 的 pipeline 成功時為 0）",
    ["project", "tag"],
    registry=registry,
)

# 最近一次成功收集 GitLab 指標的時間（Unix epoch 秒）
gitlab_last_collect_timestamp_seconds = Gauge(
    "tw_stock_gitlab_last_collect_timestamp_seconds",
    "最近一次成功收集 GitLab CI 指標的時間（Unix epoch 秒）",
    registry=registry,
)

# 動態 label 的指標，每輪重新填值前需先清空，避免舊序列殘留
_DYNAMIC_GAUGES = (
    gitlab_runner_online,
    gitlab_runner_paused,
    gitlab_runner_last_contact_seconds,
    gitlab_pipeline_status,
    gitlab_pipeline_timestamp_seconds,
    gitlab_failed_jobs,
    gitlab_tag_pipeline_status,
    gitlab_tag_undeployed_seconds,
)


# ── 資料蒐集 ──────────────────────────────────────────────────────────

def fetch_runner_states(client, group_id, now, logger=None):
    """取得群組可用 runner 的註冊狀態。

    列表 API 只回傳 `online` 旗標（GitLab 需累積數小時未聯繫才判定
    offline），故額外查詢單一 runner 詳情取得 `contacted_at`，用以計算
    「多久沒 polling」這個更即時的訊號。詳情查詢失敗時僅略過該欄位。

    Args:
        client: `GitLabClient` 實例。
        group_id: 群組 ID。
        now: 現在時間（Unix epoch 秒）。
        logger: Logger 實例，可為 None。

    Returns:
        list: 每個 runner 一個 dict，含 id/description/online/paused/
            last_contact_seconds（無法取得時為 None）。

    Raises:
        GitLabAPIError: 列表查詢失敗。
    """
    runners = client.get(
        "/groups/{}/runners".format(group_id),
        params={"per_page": 100},
    )

    states = []
    for runner in runners or []:
        runner_id = str(runner.get("id", ""))
        state = {
            "id": runner_id,
            "description": runner.get("description") or "",
            "online": bool(runner.get("online")),
            "paused": bool(runner.get("paused")),
            "last_contact_seconds": None,
        }

        try:
            detail = client.get("/runners/{}".format(runner_id))
            contacted_at = parse_iso_timestamp(detail.get("contacted_at"))
            if contacted_at is not None:
                state["last_contact_seconds"] = max(0.0, now - contacted_at)
        except GitLabAPIError as exc:
            if logger is not None:
                logger.debug("無法取得 runner %s 詳情: %s", runner_id, exc)

        states.append(state)
    return states


def fetch_project_state(client, project, now,
                        window_seconds=DEFAULT_JOB_WINDOW_HOURS * 3600,
                        job_page_size=DEFAULT_JOB_PAGE_SIZE):
    """取得單一專案的 CI 狀態。

    Args:
        client: `GitLabClient` 實例。
        project: 專案 dict（至少含 `id` 與 `path`）。
        now: 現在時間（Unix epoch 秒）。
        window_seconds: 失敗 job 統計窗口（秒）。
        job_page_size: 每個專案抓取的最近失敗 job 數上限。

    Returns:
        dict: 專案 CI 狀態；專案完全沒有 pipeline（代表未設定 CI）時
            回傳 None。

    Raises:
        GitLabAPIError: 任一 API 查詢失敗。
    """
    project_id = project.get("id")
    name = project.get("path") or str(project_id)

    pipelines = client.get(
        "/projects/{}/pipelines".format(project_id),
        params={"per_page": 1},
    )
    if not pipelines:
        # 沒有任何 pipeline 紀錄＝該專案未設定 CI（例如純客戶端 repo），
        # 不產生 pipeline / tag 指標，避免長期誤報。
        return None

    latest = pipelines[0]
    state = {
        "project": name,
        "ref": latest.get("ref") or "",
        "status": latest.get("status") or PIPELINE_STATUS_MISSING,
        "timestamp": parse_iso_timestamp(latest.get("created_at")) or 0,
        "failed_jobs": {},
        "tag": None,
        "tag_status": None,
        "tag_undeployed_seconds": 0,
    }

    # 近期失敗 job 依 failure_reason 分類
    jobs = client.get(
        "/projects/{}/jobs".format(project_id),
        params={"scope[]": "failed", "per_page": job_page_size},
    )
    for job in jobs or []:
        finished = parse_iso_timestamp(
            job.get("finished_at") or job.get("created_at")
        )
        if finished is None or now - finished > window_seconds:
            continue
        reason = job.get("failure_reason") or "unknown"
        state["failed_jobs"][reason] = state["failed_jobs"].get(reason, 0) + 1

    # 最新 tag 是否真的部署成功
    tags = client.get(
        "/projects/{}/repository/tags".format(project_id),
        params={"order_by": "updated", "sort": "desc", "per_page": 1},
    )
    if tags:
        tag = tags[0]
        tag_name = tag.get("name") or ""
        tag_time = parse_iso_timestamp(
            tag.get("created_at") or (tag.get("commit") or {}).get("created_at")
        )

        if state["ref"] == tag_name:
            tag_status = state["status"]
        else:
            tag_pipelines = client.get(
                "/projects/{}/pipelines".format(project_id),
                params={"ref": tag_name, "per_page": 1},
            )
            tag_status = (
                tag_pipelines[0].get("status")
                if tag_pipelines else PIPELINE_STATUS_MISSING
            )

        state["tag"] = tag_name
        state["tag_status"] = tag_status or PIPELINE_STATUS_MISSING
        if state["tag_status"] == "success" or tag_time is None:
            state["tag_undeployed_seconds"] = 0
        else:
            state["tag_undeployed_seconds"] = max(0.0, now - tag_time)

    return state


def _update_runner_metrics(states):
    """把 runner 狀態寫進 Prometheus 指標。

    Args:
        states: `fetch_runner_states` 的回傳值。
    """
    online_count = 0
    for state in states:
        labels = {
            "runner_id": state["id"],
            "description": state["description"],
        }
        gitlab_runner_online.labels(**labels).set(1 if state["online"] else 0)
        gitlab_runner_paused.labels(**labels).set(1 if state["paused"] else 0)
        if state["last_contact_seconds"] is not None:
            gitlab_runner_last_contact_seconds.labels(**labels).set(
                state["last_contact_seconds"]
            )
        if state["online"] and not state["paused"]:
            online_count += 1

    gitlab_runners_total.set(len(states))
    gitlab_runners_online_total.set(online_count)


def _update_project_metrics(state):
    """把單一專案的 CI 狀態寫進 Prometheus 指標。

    Args:
        state: `fetch_project_state` 的回傳值。
    """
    project = state["project"]
    gitlab_pipeline_status.labels(
        project=project, ref=state["ref"], status=state["status"]
    ).set(1)
    gitlab_pipeline_timestamp_seconds.labels(project=project).set(
        state["timestamp"]
    )

    for reason, count in state["failed_jobs"].items():
        gitlab_failed_jobs.labels(
            project=project, failure_reason=reason
        ).set(count)

    if state["tag"]:
        gitlab_tag_pipeline_status.labels(
            project=project, tag=state["tag"], status=state["tag_status"]
        ).set(1)
        gitlab_tag_undeployed_seconds.labels(
            project=project, tag=state["tag"]
        ).set(state["tag_undeployed_seconds"])


def collect_gitlab_ci(logger, client, group_id=DEFAULT_GROUP_ID, now=None,
                      window_hours=DEFAULT_JOB_WINDOW_HOURS):
    """收集 GitLab CI 基礎設施狀態並更新 Prometheus 指標。

    先把資料全部取回再一次覆寫指標；群組層級查詢失敗時只把
    `tw_stock_gitlab_api_up` 設為 0 並保留上一輪數值，避免
    「監控自己連不上 GitLab」被誤讀成「所有 pipeline 都壞了」。

    Args:
        logger: Logger 實例。
        client: `GitLabClient` 實例。
        group_id: 要掃描的 GitLab 群組 ID。
        now: 現在時間（Unix epoch 秒），預設取系統時間（方便測試）。
        window_hours: 失敗 job 統計窗口（小時）。

    Returns:
        bool: 是否成功收集。
    """
    current_time = time.time() if now is None else now
    window_seconds = window_hours * 3600

    try:
        runner_states = fetch_runner_states(
            client, group_id, current_time, logger
        )
        projects = client.get(
            "/groups/{}/projects".format(group_id),
            params={"per_page": 100, "archived": "false"},
        )
    except GitLabAPIError as exc:
        gitlab_api_up.set(0)
        logger.error("GitLab API 查詢失敗，CI 指標維持上一輪數值: %s", exc)
        return False

    project_states = []
    for project in projects or []:
        try:
            state = fetch_project_state(
                client, project, current_time, window_seconds
            )
        except GitLabAPIError as exc:
            logger.error(
                "查詢專案 %s 的 CI 狀態失敗: %s",
                project.get("path"), exc,
            )
            continue
        if state is not None:
            project_states.append(state)

    for gauge in _DYNAMIC_GAUGES:
        gauge.clear()

    _update_runner_metrics(runner_states)
    for state in project_states:
        _update_project_metrics(state)

    gitlab_api_up.set(1)
    gitlab_last_collect_timestamp_seconds.set(current_time)

    offline = [s["id"] for s in runner_states if not s["online"]]
    if offline:
        logger.warning("GitLab runner 未上線: %s", ", ".join(offline))
    if not runner_states:
        logger.error("GitLab 群組 %s 沒有任何可用 runner", group_id)

    for state in project_states:
        stuck = state["failed_jobs"].get(STUCK_FAILURE_REASON, 0)
        if stuck:
            logger.error(
                "專案 %s 近 %d 小時有 %d 個 job 因無可用 runner 卡住",
                state["project"], window_hours, stuck,
            )
        if state["tag_undeployed_seconds"]:
            logger.warning(
                "專案 %s 的 tag %s 尚未成功部署（狀態=%s，已過 %.0f 秒）",
                state["project"], state["tag"], state["tag_status"],
                state["tag_undeployed_seconds"],
            )

    logger.info(
        "GitLab CI 指標收集完成：runner %d/%d online、專案 %d 個",
        sum(1 for s in runner_states if s["online"]),
        len(runner_states), len(project_states),
    )
    return True
