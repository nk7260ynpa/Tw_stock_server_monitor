# 台股伺服器監控 (Tw Stock Server Monitor)

台股伺服器監控工具，內建 Grafana + Prometheus 監控系統，
即時監控主機 CPU、記憶體、磁碟、網路等資源使用量，
持續檢查各 Tw_stock 微服務的健康狀態，
並監控 **CI/CD 基礎設施**（GitLab Runner 容器、runner 註冊狀態、pipeline
與 tag 部署結果）是否無聲無息地停擺。

支援 **macOS** 與 **Linux** 兩種部署環境。

## 專案架構

```text
Tw_stock_server_monitor/
├── docker/
│   ├── build.sh                          # 建立 Docker image 的執行腳本
│   ├── Dockerfile                        # Docker image 定義
│   ├── docker-compose.yaml               # Docker Compose 設定（含監控服務）
│   ├── prometheus/
│   │   ├── prometheus.yml                # Prometheus 設定檔
│   │   └── rules/
│   │       ├── ci_alerts.yml             # CI/CD 基礎設施告警規則
│   │       └── service_alerts.yml        # 微服務與監控自身告警規則
│   └── grafana/
│       └── provisioning/
│           ├── datasources/
│           │   └── datasource.yml        # Grafana 資料來源設定
│           └── dashboards/
│               ├── dashboard.yml         # Grafana 儀表板 provider 設定
│               ├── node-exporter.json    # 主機資源監控儀表板
│               └── ci-cd.json            # CI/CD 基礎設施監控儀表板
├── logs/                                 # 日誌檔案目錄
├── src/
│   ├── __init__.py
│   ├── docker_monitor.py                 # Docker 容器存活監控（走 docker.sock）
│   ├── gitlab_monitor.py                 # GitLab CI 基礎設施監控（REST API）
│   ├── logger.py                         # 日誌設定模組
│   ├── macos_exporter.py                 # macOS 主機指標 Exporter
│   ├── main.py                           # 主程式
│   ├── registry.py                       # 共用的 Prometheus CollectorRegistry
│   └── timeutils.py                      # ISO 8601 時間字串解析工具
├── tests/
│   ├── __init__.py
│   ├── prometheus/
│   │   ├── ci_alerts_test.yml            # CI 告警規則的 promtool 測試
│   │   └── service_alerts_test.yml       # 服務告警規則的 promtool 測試
│   ├── test_alert_rules.py               # 告警規則結構與覆蓋率測試
│   ├── test_docker_monitor.py            # 容器監控單元測試
│   ├── test_gitlab_monitor.py            # GitLab CI 監控單元測試
│   ├── test_macos_exporter.py            # macOS Exporter 單元測試
│   ├── test_main.py                      # 主程式單元測試
│   └── test_timeutils.py                 # 時間解析工具單元測試
├── .gitignore
├── LICENSE
├── README.md
├── pyproject.toml                        # Python 套件定義（PEP 621）
├── requirements.txt                      # Docker 環境完整釘版依賴
├── run.sh                                # 啟動所有服務的執行腳本
└── run_macos_exporter.sh                 # 單獨啟動 macOS Exporter
```

## 監控架構

### macOS 環境

```text
                              ┌──────────────────┐    psutil    ┌──────────┐
                     ┌────────│  macOS Exporter   │◄────────────│  macOS   │
                     │        │    :9101          │             │  主機系統  │
                     │        └──────────────────┘             └──────────┘
┌──────────────┐     │
│  Prometheus  │◄────┤
│   :9090      │     │        ┌──────────────────┐    TCP check
└──────┬───────┘     └────────│  Service Monitor  │──────────────► 各 Tw_stock 服務
       │ 查詢                 │    :9102          │
┌──────▼───────┐              └──────────────────┘
│   Grafana    │
│   :3000      │
└──────────────┘
```

- **macOS Exporter**：透過 psutil 收集 macOS 主機真實指標，在主機上直接執行
- **Service Monitor**：在 Docker 中持續運行，定期 TCP 檢查各 Tw_stock 服務的健康狀態

### Linux 伺服器環境

```text
                              ┌────────────────┐    掛載磁碟    ┌──────────┐
                     ┌────────│  Node Exporter  │◄──────────────│  Linux   │
                     │        │    :9100        │   /proc /sys  │  主機系統  │
                     │        └────────────────┘               └──────────┘
┌──────────────┐     │
│  Prometheus  │◄────┤
│   :9090      │     │        ┌──────────────────┐    TCP check
└──────┬───────┘     └────────│  Service Monitor  │──────────────► 各 Tw_stock 服務
       │ 查詢                 │    :9102          │
┌──────▼───────┐              └──────────────────┘
│   Grafana    │
│   :3000      │
└──────────────┘
```

- **Node Exporter**：在 Docker 中執行，掛載主機 /proc 與 /sys 收集指標
- **Service Monitor**：在 Docker 中持續運行，定期 TCP 檢查各 Tw_stock 服務的健康狀態

### CI/CD 基礎設施監控

Service Monitor 除了 TCP 探測，還額外走兩條路蒐集「CI 到底跑不跑得動」：

```text
                        ┌──────────────────┐  docker.sock(ro)  ┌────────────────┐
              ┌─────────│  Service Monitor  │──────────────────►│ gitlab-runner  │
              │         │     :9102         │   容器狀態          │ gitlab 等容器   │
┌──────────────┐        │                   │  GitLab REST API  ┌────────────────┐
│  Prometheus  │◄───────│                   │──────────────────►│ 自架 GitLab     │
│    :9090     │        └──────────────────┘  runner / pipeline │  :8080         │
└──────┬───────┘                              / tag 部署狀態     └────────────────┘
       │ 告警規則（rules/*.yml）
┌──────▼───────┐
│   Grafana    │  ← 「CI/CD 基礎設施監控」儀表板
│    :3000     │
└──────────────┘
```

**設計緣由**：2026-07 曾發生 `gitlab-runner` 容器 **Exit 0** 後靜默四週、
所有 tag 都沒真正部署卻零告警的事故。因此監控刻意做成**分層互為備援**：

| 層級 | 訊號 | 偵測延遲 | 說明 |
|------|------|---------|------|
| 容器 | `tw_stock_container_up` | 約 5 分鐘 | 最快。對常駐服務而言 **Exit 0 也算異常** |
| 心跳 | `tw_stock_gitlab_runner_last_contact_seconds` | 約 105 分鐘 | GitLab 最多每 40 分鐘才寫一次 `contacted_at` |
| 註冊 | `tw_stock_gitlab_runner_online` | 約 2 小時 15 分 | GitLab 自身的 offline 判定，最慢但最權威 |
| 業務 | 卡住的 job／未部署的 tag | 5～10 分鐘 | 直接反映「job 沒人接」「版本沒上線」 |

> 容器狀態採**唯讀掛載 `/var/run/docker.sock`** 取得，而非啟用 runner 自身的
> metrics endpoint。原因：`gitlab-runner` 不 publish 任何 port，TCP 探測無效；
> 而啟用 metrics 需修改 runner 設定檔（不屬於本 repo）並重啟 runner，且
> **runner 行程一旦死掉就再也回不了自己的 metrics**——容器狀態則不受此限。

## 環境需求

- Docker
- Docker Compose
- macOS 環境額外需要：Python 3、pip（安裝 psutil 與 prometheus_client）

## 快速開始

### 1. 啟動所有服務

```bash
bash run.sh
```

在 macOS 上執行時，會自動偵測並透過 launchd 啟動 macOS Exporter（開機自啟、當機自重啟）。

### 2. 開啟 Grafana 儀表板

瀏覽器開啟 <http://localhost:3000>

- 帳號：`admin`
- 密碼：`admin`

進入後點選左側選單 **Dashboards**，即可看到自動載入的兩張儀表板：

- **主機資源監控**：CPU／記憶體／磁碟／網路與程序排行榜。
- **CI/CD 基礎設施監控**：觸發中告警、GitLab Runner 容器狀態、online runner
  數、各專案 pipeline 狀態、因無可用 runner 卡住的 job、尚未成功部署的 tag。

### 3. 切換資料來源

儀表板頂部有 **Job** 下拉選單，可切換：

- `macos-exporter`：macOS 主機真實指標（預設）
- `node-exporter`：Node Exporter 指標（Linux 伺服器適用）

### 4. 服務端口

| 服務 | 端口 | 說明 |
|------|------|------|
| Grafana | 3000 | 監控儀表板 |
| Prometheus | 9090 | 指標儲存與查詢 |
| Node Exporter | 9100 | Docker/Linux 主機指標收集 |
| macOS Exporter | 9101 | macOS 主機指標收集 |
| Service Monitor | 9102 | Tw_stock 服務健康檢查指標 |

## 監控指標

### 主機資源監控

儀表板包含以下監控面板：

- **總覽**：CPU 使用率、記憶體使用率、磁碟使用率、系統運行時間
- **CPU**：使用率趨勢、各模式使用率（user/system/iowait/nice/irq/softirq/steal）、系統負載
- **記憶體**：使用量、細項分類（應用程式/Buffers/Cached/Free）、Swap 使用量、記憶體使用量前 15 名程序（表格）
- **磁碟**：各分區使用率、I/O 讀寫速率
- **網路**：流量趨勢、封包速率

### 服務健康檢查

Service Monitor 持續檢查以下 11 個 Tw_stock 微服務的 TCP 連線狀態
（清單定義於 `src/main.py` 的 `MONITORED_SERVICES`）：

| 服務 | 容器名稱（host） | 端口 |
|------|-----------------|------|
| crawler | tw_stocker_crawler | 6738 |
| mysql | tw_stock_database | 3306 |
| db_operating | tw_stock_db_operating | 8080 |
| indicator | tw-stock-indicator | 5001 |
| ml | tw-stock-ml | 5002 |
| tools | tw_stock_tools | 8000 |
| dashboard | tw_stock_dashboard | 8000 |
| webpage | tw-stock-webpage | 8000 |
| news | tw_stock_news | 8003 |
| hot | tw_stock_hot | 5050 |
| specialinfo | tw-stock-specialinfo | 5055 |

暴露的 Prometheus 指標：

- `tw_stock_service_up`：服務健康狀態（1=正常, 0=異常）
- `tw_stock_service_response_time_seconds`：TCP 連線回應時間（秒）

### CI/CD 基礎設施指標

#### 容器存活（`src/docker_monitor.py`）

透過唯讀掛載的 `/var/run/docker.sock` 查詢容器狀態，適用於**不 publish port、
TCP 探測不到**的 CI 容器。監控對象由 `MONITOR_CONTAINERS` 指定，預設
`gitlab-runner,gitlab`。

- `tw_stock_container_up{container}`：容器是否為 running（1/0）。
  **`exited` 一律為 0，包含 Exit 0**——對常駐服務而言正常退出同樣是異常。
- `tw_stock_container_state{container,state}`：容器細部狀態，state 為
  `running`／`exited`／`paused`／`missing` 等固定列舉，命中者為 1、其餘為 0
  （固定列舉可避免舊狀態序列殘留造成誤報）。
- `tw_stock_container_exit_code{container}`：最後一次結束碼。
- `tw_stock_container_start_timestamp_seconds{container}`：啟動時間。
- `tw_stock_container_restart_policy_always{container}`：restart policy 是否為
  `always`（1/0）。`unless-stopped` 的語意是「被明確 stop 過就不自動拉起」，
  值為 0 時代表該容器停掉後不會自己回來。
- `tw_stock_docker_api_up`：Docker API 是否可存取（1/0）。**全部查詢都失敗時
  只降此指標並保留上一輪數值**，避免「監控自己壞了」被誤讀成「容器全掛」。

#### GitLab CI 狀態（`src/gitlab_monitor.py`）

以 GitLab REST API v4 掃描 twstock 群組（預設 `GITLAB_GROUP_ID=38`）：

- `tw_stock_gitlab_runner_online{runner_id,description}`：GitLab 認定的 online 狀態。
- `tw_stock_gitlab_runner_paused{runner_id,description}`：是否被暫停。
- `tw_stock_gitlab_runner_last_contact_seconds{runner_id,description}`：
  最後聯繫至今秒數。
- `tw_stock_gitlab_runners_online_total` / `tw_stock_gitlab_runners_total`：
  online 與註冊總數，前者為 0 代表**任何 pipeline 都不會被執行**。
- `tw_stock_gitlab_pipeline_status{project,ref,status}`：各專案最新 pipeline 狀態。
- `tw_stock_gitlab_pipeline_timestamp_seconds{project}`：最新 pipeline 建立時間。
- `tw_stock_gitlab_failed_jobs{project,failure_reason}`：近 24 小時失敗 job 數，
  依 `failure_reason` 分類。**`stuck_pending_no_matching_runners` 代表沒有
  runner 可接（基礎設施問題，重跑無效），與 `script_failure`（程式碼問題）
  可明確區分。**
- `tw_stock_gitlab_tag_pipeline_status{project,tag,status}`：最新版本 tag 對應
  pipeline 的狀態，`missing` 代表該 tag 根本沒有產生 pipeline。
- `tw_stock_gitlab_tag_undeployed_seconds{project,tag}`：最新 tag 尚未成功部署的
  秒數（成功時為 0）。這是事故最直接的業務影響指標。
- `tw_stock_gitlab_api_up`、`tw_stock_gitlab_token_configured`、
  `tw_stock_gitlab_last_collect_timestamp_seconds`：監控自身健康。

### 告警規則

規則檔位於 `docker/prometheus/rules/`，由 `prometheus.yml` 的
`rule_files: /etc/prometheus/rules/*.yml` 載入。目前尚未接 Alertmanager，
告警會以 `ALERTS` 序列呈現在 Prometheus 與 Grafana 的「CI/CD 基礎設施監控」
儀表板上。

| 告警 | 嚴重度 | 觸發條件（`for`） |
|------|--------|------------------|
| `GitLabRunnerContainerDown` | critical | gitlab-runner 容器非 running（5m） |
| `CIContainerDown` | warning | 其他受監控 CI 容器非 running（10m） |
| `DockerApiUnreachable` | warning | 無法存取 Docker API（10m） |
| `GitLabNoOnlineRunner` | critical | online runner 數為 0（10m） |
| `GitLabRunnerOffline` | critical | 個別 runner 被 GitLab 判定 offline（15m） |
| `GitLabRunnerNoContact` | warning | runner 逾 90 分鐘未聯繫（15m） |
| `GitLabRunnerPaused` | warning | runner 被暫停（30m） |
| `GitLabJobsStuckNoMatchingRunner` | critical | 有 job 因無可用 runner 卡住（5m） |
| `GitLabPipelineFailed` | warning | 最新 pipeline 失敗（10m） |
| `GitLabTagNotDeployed` | critical | tag 建立逾 30 分鐘仍未成功部署（10m） |
| `GitLabApiUnreachable` | warning | 已設權杖但 GitLab API 查詢失敗（15m） |
| `GitLabTokenMissing` | warning | 未設定 GitLab 權杖（1h） |
| `GitLabCollectorStalled` | warning | GitLab 指標逾 30 分鐘未更新（10m） |
| `TwStockServiceDown` | critical | 微服務 TCP 探測連續失敗（5m） |
| `PrometheusTargetDown` | warning | Prometheus 抓取目標失效（10m） |
| `ServiceMonitorMetricsMissing` | critical | 完全找不到 Service Monitor 指標（10m） |

> `GitLabJobsStuckNoMatchingRunner`（基礎設施）與 `GitLabPipelineFailed`
> （程式碼）刻意分成兩條規則且嚴重度不同：前者重跑 pipeline 沒有用，必須先修
> runner；兩者同時出現時應優先處理前者。

### 設定 GitLab API 權杖

CI 監控需要一組具 **`read_api`** scope 的權杖。**權杖不得寫進程式碼或
commit**，一律由環境變數提供，程式也不會把權杖寫進日誌。

1. 於 GitLab 建立權杖：群組 `twstock` → **Settings → Access Tokens**
   （或個人 **Edit profile → Access Tokens**），scope 勾選 `read_api`。
2. 提供給 Service Monitor，二擇一：

   - **環境變數 `GITLAB_TOKEN`**：本機開發時建立 `docker/.env`（已列入
     `.gitignore`，不會被 commit）：

     ```bash
     # docker/.env
     GITLAB_TOKEN=glpat-xxxxxxxxxxxx
     ```

   - **檔案掛載 `GITLAB_TOKEN_FILE`**：把權杖檔以唯讀方式掛進容器，再指定
     其路徑（適合 Docker secret 情境）：

     ```bash
     GITLAB_TOKEN_FILE=/run/secrets/gitlab_token
     ```

3. 正式部署（CI）：在 GitLab 專案 **Settings → CI/CD → Variables** 新增
   **masked** 變數 `MONITOR_GITLAB_TOKEN`，`.gitlab-ci.yml` 會在 `deploy` 時
   以 `-e GITLAB_TOKEN=` 傳入容器。**切勿把權杖寫進 `.gitlab-ci.yml`。**

未設定權杖時，CI 監控只會停用 GitLab 那一半（容器存活監控仍運作），並由
`GitLabTokenMissing` 告警提醒——**不會靜默失效**。

相關環境變數：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `MONITOR_CONTAINERS` | `gitlab-runner,gitlab` | 監控存活的容器名稱（逗號分隔，空字串停用） |
| `GITLAB_URL` | `http://host.docker.internal:8080` | 自架 GitLab 網址。**容器內不可用 `127.0.0.1`**，那是容器自己 |
| `GITLAB_TOKEN` | 空 | `read_api` 權杖 |
| `GITLAB_TOKEN_FILE` | 空 | 權杖檔路徑（`GITLAB_TOKEN` 未設時才讀） |
| `GITLAB_GROUP_ID` | `38` | 要掃描的群組（twstock） |
| `GITLAB_CHECK_INTERVAL` | `300` | GitLab API 收集間隔（秒） |

### 程序排行榜指標（macOS Exporter）

macOS Exporter 額外暴露 per-process 排行榜指標，每次抓取時動態生成，取前 15 名：

#### 記憶體使用量排行

- `node_top_memory_process_rss_bytes`：程序 RSS 記憶體使用量（bytes），包含 labels：`process_name`、`pid`、`rank`

#### 網路流量排行

- `node_top_network_process_bytes`：程序網路流量（bytes），包含 labels：`process_name`、`pid`、`rank`、`direction`（in/out）
- 資料來源：macOS 內建 `nettop` 命令，依總流量（bytes_in + bytes_out）排序

#### 耗電量排行

- `node_top_power_process_energy`：程序 energy impact 值，包含 labels：`process_name`、`pid`、`rank`
- 資料來源：macOS 內建 `top` 命令的 power 欄位

## 其他操作

### 僅建立主程式 Docker image

```bash
bash docker/build.sh
```

### 管理 macOS Exporter

```bash
# 啟動/重啟（透過 launchd，開機自啟、當機自重啟）
bash run_macos_exporter.sh

# 查看服務狀態
bash run_macos_exporter.sh status

# 停止服務並移除開機自啟
bash run_macos_exporter.sh stop
```

### 停止所有服務

```bash
docker compose -f docker/docker-compose.yaml down
# macOS 環境另需停止 exporter
bash run_macos_exporter.sh stop
```

### 停止並清除資料

```bash
docker compose -f docker/docker-compose.yaml down -v
```

### 套用新的告警規則

規則檔以 bind mount 掛進 Prometheus（`./prometheus/rules:/etc/prometheus/rules:ro`）。
**CI 的 `deploy` job 只重啟 Service Monitor，不會動 Prometheus**，因此新增或
修改規則後需手動讓 Prometheus 重新載入：

```bash
# 首次新增掛載點時必須重建容器
docker compose -f docker/docker-compose.yaml up -d prometheus

# 僅修改規則內容時，重啟即可
docker restart prometheus

# 確認規則已載入
docker exec prometheus wget -qO- http://localhost:9090/api/v1/rules | head
```

## 執行測試

### Python 單元測試

```bash
docker run --rm -v "$(pwd)":/app -w /app nk7260ynpa/tw-stock-monitor:latest \
  python -m pytest tests/ -v
```

### 告警規則測試（promtool）

告警規則以 `promtool test rules` 驗證「什麼條件下真的會觸發」，
含正例與反例（例如 `script_failure` 單獨出現時**不**可觸發基礎設施告警）：

```bash
docker run --rm -v "$(pwd)":/work -w /work --entrypoint promtool \
  prom/prometheus:latest test rules tests/prometheus/ci_alerts_test.yml

docker run --rm -v "$(pwd)":/work -w /work --entrypoint promtool \
  prom/prometheus:latest test rules tests/prometheus/service_alerts_test.yml
```

`tests/test_alert_rules.py` 另會檢查**每條告警都有對應的 promtool 測試**，
新增規則卻忘記補測試時會直接失敗。

## CI/CD（自動部署 + GitHub 鏡像）

本專案以 GitLab 為主要儲存庫，`.gitlab-ci.yml` 在 `main` 打上 `vX.Y.Z` 版本
tag 時觸發兩條 job（合併進 `main` 當下**不會**觸發任何管線）：

1. **`deploy`**：重新建置並重啟 **Service Monitor 容器（僅此一顆）**。
2. **`mirror-to-github`**：把 `main` 與該版本 tag 一併鏡像到 GitHub。

### deploy（只重啟 Service Monitor，不碰其他容器）

GitLab Runner 為 docker executor 並掛載 `/var/run/docker.sock`，故 `deploy`
job 內的 `docker` 指令直接作用在 host 的 Docker daemon。`deploy` 嚴格維持
**build → `rm -f` → run** 順序，且每一步的目標**只有 Service Monitor 容器**：

```text
docker build  →  docker rm -f tw-stock-server-monitor  →  docker run（新 image）
```

> **重要特例**：本 repo 為多容器監控堆疊，但**只有 Service Monitor 這顆 image
> 由本 repo build**；Prometheus / Grafana / Node Exporter 皆為上游官方映像，且
> compose 內以相對路徑 bind 掛載設定檔。因此 `deploy` **只 build + 重啟
> `tw-stock-server-monitor` 一顆容器**，**絕不**`docker rm` 或重啟
> prometheus / grafana / node-exporter，也**不**執行 `docker compose`、不動
> launchd / macOS 原生 exporter。

部署細節：

- **image**：`nk7260ynpa/tw-stock-monitor`，同時打 `:vX.Y.Z`（不含 `v`）與
  `:latest` 兩個 tag。
- **容器**：`tw-stock-server-monitor`，`--restart=always`。
- **網路**：`--network db_network`，讓 Service Monitor 對其他 Tw_stock 服務做
  TCP 探測，並讓 Prometheus 以容器名 `tw-stock-server-monitor:9102` 在
  `db_network` 上抓取指標。
- **port**：`--expose 9102`（僅容器內部暴露、不對外 publish，與 compose 一致）。
- **掛載**：`/var/run/docker.sock:ro`，供容器存活監控查詢 `gitlab-runner`
  等不 publish port 的 CI 容器。
- **環境變數**：`MONITOR_METRICS_PORT=9102`、`MONITOR_CHECK_INTERVAL=30`、
  `MONITOR_CHECK_TIMEOUT=5`、`MONITOR_CONTAINERS`、`GITLAB_URL`、
  `GITLAB_GROUP_ID`、`GITLAB_CHECK_INTERVAL`，以及由 **masked CI/CD 變數**
  `MONITOR_GITLAB_TOKEN` 傳入的 `GITLAB_TOKEN`（權杖不寫在 `.gitlab-ci.yml`）。
- **logs**：改用**具名 volume** `tw-stock-server-monitor_logs:/app/logs`
  （取代 compose 的相對 bind 掛載，避免 socket-bound runner 內相對路徑失效）。
  查看日誌用 `docker logs tw-stock-server-monitor`。

### mirror-to-github

- **鏡像內容**：管線把 `main` 與該版本 tag 一併推送到 GitHub。
- 認證使用 Runner 注入的 `GITHUB_SSH_KEY`（對應公鑰需加到 GitHub repo 的
  Deploy keys 並開啟 Allow write access）。

亦即：合併 MR 進 `main` 後，需另外打上 `vX.Y.Z` annotated tag 並 push 到
GitLab，才會觸發 `deploy`（重啟 Service Monitor）與 `mirror-to-github`（鏡像）。

## 授權條款

詳見 [LICENSE](LICENSE) 檔案。
