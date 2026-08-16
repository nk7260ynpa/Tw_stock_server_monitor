# 台股伺服器監控 (Tw Stock Server Monitor)

台股伺服器監控工具，內建 Grafana + Prometheus 監控系統，
即時監控主機 CPU、記憶體、磁碟、網路等資源使用量，
持續檢查各 Tw_stock 微服務的健康狀態，
並監控 **CI/CD 基礎設施**（GitLab Runner 容器、runner 註冊狀態、pipeline
與 tag 部署結果）是否無聲無息地停擺，
再經 **Alertmanager** 分組、抑制後推播出去（一次事故只發一則通知）。

支援 **macOS** 與 **Linux** 兩種部署環境。

## 專案架構

```text
Tw_stock_server_monitor/
├── docker/
│   ├── build.sh                          # 建立 Docker image 的執行腳本
│   ├── Dockerfile                        # Docker image 定義
│   ├── docker-compose.yaml               # Docker Compose 設定（含監控服務）
│   ├── alertmanager/
│   │   ├── alertmanager.yml              # 告警路由、分組與抑制規則
│   │   └── secrets/                      # 外部管道憑證（不進版控）
│   ├── prometheus/
│   │   ├── prometheus.yml                # Prometheus 設定檔
│   │   └── rules/
│   │       ├── ci_alerts.yml             # CI/CD 基礎設施告警規則
│   │       ├── notification_alerts.yml   # 推播鏈路自身的告警規則
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
│   └── alerts/                           # 告警通知落地（JSON Lines）
├── scripts/
│   ├── alert_drill.sh                    # 告警推播鏈路端對端演練
│   └── drill/
│       ├── assert_drill.py               # 演練驗收（抑制是否真的生效）
│       ├── run_receiver.py               # 演練用的接收器行程
│       └── serve_metrics.py              # 演練用的事故指標端點
├── src/
│   ├── __init__.py
│   ├── alert_receiver.py                 # Alertmanager webhook 接收器
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
│   │   ├── notification_alerts_test.yml  # 推播鏈路告警的 promtool 測試
│   │   └── service_alerts_test.yml       # 服務告警規則的 promtool 測試
│   ├── test_alert_receiver.py            # webhook 接收器單元測試
│   ├── test_alert_rules.py               # 告警規則結構與覆蓋率測試
│   ├── test_alertmanager_config.py       # 路由與抑制規則的結構測試
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

> 容器狀態透過掛載 `/var/run/docker.sock` 取得，而非啟用 runner 自身的
> metrics endpoint。原因：`gitlab-runner` 不 publish 任何 port，TCP 探測無效；
> 而啟用 metrics 需修改 runner 設定檔（不屬於本 repo）並重啟 runner，且
> **runner 行程一旦死掉就再也回不了自己的 metrics**——容器狀態則不受此限。
>
> **安全提醒**：掛載 docker.sock 等同把 host 的 Docker daemon 交給容器，
> 實質等於 host root 權限。掛載參數的 `:ro` 只讓 **socket 檔案節點**唯讀，
> **並不會限制經由該 socket 送出的 API 動詞**，不是安全邊界。本專案的程式
> 只發 `GET`（見 `src/docker_monitor.py`），若要真正限制權限，需改接
> 只放行 `GET /containers/*/json` 的 socket proxy。

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
  數、各專案 pipeline 狀態、因無可用 runner 卡住的 job、尚未成功部署的 tag，
  以及**告警推播鏈路**（Alertmanager／接收器狀態、心跳新鮮度、24h 通知數）。

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
| Alertmanager | 9093 | 告警分組、抑制與推播 |
| 告警接收器 | 9103 | 本地推播管道（Alertmanager webhook） |

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
- `tw_stock_last_check_timestamp_seconds`：最近一次完成健康檢查循環的時間。
  Gauge 不會過期，主循環卡住時 `tw_stock_service_up` 會停在舊值看似正常，
  必須靠這個指標才看得出「監控自己不動了」。

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
- `tw_stock_gitlab_tag_pipeline_status{project,tag,status}`：最新版本 tag 的
  **部署狀態**。取該 tag pipeline 內 `deploy` job 的狀態，而非整條 pipeline
  ——tag pipeline 另含互不相依的 `mirror-to-github`，鏡像失敗不代表版本沒
  上線，用 pipeline 狀態會造成永久誤報。沒有 `deploy` job 的專案退回
  pipeline 狀態，`missing` 代表該 tag 根本沒有產生 pipeline。
- `tw_stock_gitlab_tag_undeployed_seconds{project,tag}`：最新 tag 尚未成功部署的
  秒數（成功時為 0）。這是事故最直接的業務影響指標。
- `tw_stock_gitlab_api_up`、`tw_stock_gitlab_token_configured`、
  `tw_stock_gitlab_last_collect_timestamp_seconds`：監控自身健康。

### 告警規則

規則檔位於 `docker/prometheus/rules/`，由 `prometheus.yml` 的
`rule_files: /etc/prometheus/rules/*.yml` 載入。觸發的告警會送往
**Alertmanager**（分組、抑制後推播，見「告警推播管道」），同時以 `ALERTS`
序列呈現在 Prometheus 與 Grafana 的「CI/CD 基礎設施監控」儀表板上。

| 告警 | 嚴重度 | 觸發條件（`for`） |
|------|--------|------------------|
| `GitLabRunnerContainerDown` | critical | gitlab-runner 容器非 running（5m） |
| `CIContainerDown` | warning | 其他受監控 CI 容器非 running（10m） |
| `DockerApiUnreachable` | warning | 無法存取 Docker API（10m） |
| `RunnerContainerMetricMissing` | warning | 查不到 gitlab-runner 容器指標（15m） |
| `GitLabNoOnlineRunner` | critical | 已設權杖且 API 正常，但 online runner 數為 0（10m） |
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
| `ServiceMonitorCheckStalled` | critical | 健康檢查主循環逾 5 分鐘沒完成一輪（5m） |
| `Watchdog` | info | 永遠觸發的心跳（0m），用途見下方死人開關 |
| `AlertDeliveryStalled` | critical | 逾 15 分鐘沒收到 Watchdog 心跳（5m） |
| `AlertReceiverDown` | critical | 本地告警接收器未在服務（5m） |
| `AlertReceiverMetricMissing` | warning | 查不到接收器狀態指標（15m），例如舊版本續跑 |
| `AlertmanagerDown` | critical | Alertmanager 抓不到或已停擺（5m） |
| `AlertNotificationFailing` | critical | Alertmanager 推播持續失敗（10m） |
| `PrometheusNotConnectedToAlertmanager` | critical | Prometheus 找不到任何 Alertmanager（10m） |

> `GitLabJobsStuckNoMatchingRunner`（基礎設施）與 `GitLabPipelineFailed`
> （程式碼）刻意分成兩條規則且嚴重度不同：前者重跑 pipeline 沒有用，必須先修
> runner；兩者同時出現時應優先處理前者。
>
> **排隊 ≠ 沒有 runner**：14 個專案共用同一個 runner，job 執行槽數由 runner
> `config.toml` 的**全域 `concurrent`**（目前為 `1`）決定；`request_concurrency`
> 並未出現在該設定檔中（即預設值 1），且它管的是「同時向 GitLab 取 job 的請求
> 數」，不是併發執行數，兩者不可混用。**調高 `concurrent` 並非無腦的好事**：本
> 系列 repo 的 `deploy` job 是 `build → rm -f → run`，同一個 repo 若有兩條
> pipeline 並行，會在 `rm -f` 與 `run` 之間互相踩踏；`concurrent = 1` 目前正是
> 用「全域序列化」換來這個安全性。要提高吞吐的正解是**先在各 repo 的 `deploy`
> job 加 `resource_group: deploy`**（跨 repo 可並行、同 repo 強制序列），再調高
> `concurrent`。順序反了就是拿部署正確性換速度。
> （runner 設定檔不屬於本 repo，變更需由該 repo 的負責人執行。）
>
> `ServiceMonitorMetricsMissing` 抓「監控整個不見了」，
> `ServiceMonitorCheckStalled` 抓「監控還在但已經不動了」——Gauge 不會過期，
> 主循環卡住時舊值會一直看起來是健康的，這正是本次事故「無聲失效」的同型風險。
> `RunnerContainerMetricMissing` 則補「序列根本不存在」這個缺口：
> `GitLabRunnerContainerDown` 依賴 `tw_stock_container_up` 序列，一旦
> `MONITOR_CONTAINERS` 漏掉 gitlab-runner，它只會安靜地不觸發。
>
> 依賴 GitLab API 的告警（`GitLabNoOnlineRunner`、`GitLabCollectorStalled`）
> 都以 `tw_stock_gitlab_token_configured` / `tw_stock_gitlab_api_up` 當閘門。
> 這些 Gauge 預設值是 0，且 API 失敗時 collector 直接 return、不會寫入，
> 沒有閘門就會在「權杖沒設」或「權杖過期」時永久假 critical，蓋掉真訊號；
> 真正的原因由 `GitLabTokenMissing` 與 `GitLabApiUnreachable` 各自負責。

### 告警推播管道

告警規則寫得再好，沒有推播就只是「有人去看才發現」——上次事故正是四週沒人看。
本專案以 **Alertmanager** 負責推播，並預設一條**不需要任何憑證、不依賴外部
服務**的本地管道。

```text
Prometheus ──告警──► Alertmanager ──分組/抑制──► 告警接收器 :9103
  :9090                 :9093                    （src/alert_receiver.py）
    ▲                     │                              │
    └──── 抓取 :9093 ─────┘                    logs/alerts/*.jsonl + 指標
         （互相監控）
```

#### 為何選 Alertmanager 而非 Grafana contact point

| 考量 | Alertmanager | Grafana contact point |
|------|--------------|----------------------|
| 抑制（inhibition） | 原生 `inhibit_rules`，宣告式 | **不支援**，只能靠 mute timing／notification policy 勉強近似 |
| 與現有規則的關係 | 既有規則本來就是 Prometheus 原生格式，直接沿用 | 需改寫成 Grafana 管理式告警，等於維護兩套 |
| 設定是否進版控 | `docker/alertmanager/alertmanager.yml` 進 repo，可被測試 | 設定存在 Grafana 資料庫，改動不留痕跡 |
| 是否需要憑證 | 不需要 | 對 Grafana 內建 Alertmanager 送告警需要 Grafana 認證 |

**抑制**是這次的關鍵需求（一次事故只發一則通知），Grafana 統一告警沒有等價
功能，故選 Alertmanager。多一顆容器的代價換來設定進版控、可被單元測試。

#### 分組與抑制

| 機制 | 設定 | 目的 |
|------|------|------|
| 分組 | `group_by: [component]`、`group_wait: 30s` | 同一次事故（同 component）併成一則通知 |
| critical 路由 | `group_wait: 10s`、`repeat_interval: 1h` | 重要告警更快送出、提醒更密集 |
| 心跳路由 | `repeat_interval: 2m` | 心跳必須遠快於 15 分鐘的停擺門檻 |

抑制規則只寫**因果關係**（A 發生必然導致 B），不做「critical 一律蓋掉
warning」這種粗糙抑制——後者會把不相干的問題一起消音，等於製造新盲區。

| 來源（先修這個） | 被抑制的下游告警 |
|-----------------|-----------------|
| `GitLabRunnerContainerDown` | `GitLabNoOnlineRunner`、`GitLabRunnerOffline`、`GitLabRunnerNoContact`、`GitLabRunnerPaused`、`GitLabJobsStuckNoMatchingRunner`、`GitLabPipelineFailed`、`GitLabTagNotDeployed` |
| `DockerApiUnreachable` | 所有容器狀態類告警 |
| `GitLabApiUnreachable` / `GitLabTokenMissing` / `GitLabCollectorStalled` | 所有 GitLab 來源的 CI 告警 |
| `ServiceMonitorMetricsMissing` / `ServiceMonitorCheckStalled` | 所有由 Service Monitor 指標推導的告警 |
| `AlertmanagerDown` | `AlertDeliveryStalled`、`AlertNotificationFailing` |

> 抑制最典型的失效是**安靜地不生效**：告警名稱打錯一個字，`amtool check-config`
> 照樣通過，抑制卻永遠不會發生。`tests/test_alertmanager_config.py` 因此逐一
> 比對抑制規則裡的名稱是否真的存在於規則檔，並檢查上表第一列的必要抑制集合。
>
> **已知取捨**：第一列連 `GitLabPipelineFailed` 一起抑制，因此 runner 停擺
> 期間真實的「程式碼失敗」也會被一併消音，要等 runner 復原後才會重新送出。
> 這是刻意的選擇——runner 掛掉時所有 pipeline 都會失敗，不抑制就會被上百則
> 假陽性淹沒；而 runner 一修好，仍然失敗的 pipeline 會在下一個評估週期再叫。

#### 誰來監控告警系統本身

推播鏈路自己斷掉時，**症狀就是「沒有通知」**，與「一切正常」外觀完全相同。
因此鏈路上每一段都被另一段盯著：

| 失效點 | 由誰發現 |
|--------|---------|
| Prometheus 沒接到 Alertmanager | `PrometheusNotConnectedToAlertmanager` |
| Alertmanager 掛了 | `AlertmanagerDown`（Prometheus 抓 `up{job="alertmanager"}`） |
| Alertmanager 送不出去 | `AlertNotificationFailing`（`alertmanager_notifications_failed_total`） |
| 接收器掛了 | `AlertReceiverDown`（另有 `AlertReceiverMetricMissing` 抓「指標根本不存在」，例如舊版本容器續跑） |
| 上述以外的任何一段斷掉 | `Watchdog` 心跳停止 → `AlertDeliveryStalled` |

> **注意這張表的自我指涉**：接收器是目前**唯一**的 receiver，所以
> `AlertReceiverDown` / `AlertReceiverMetricMissing` / `AlertNotificationFailing`
> 一旦觸發，它們自己也送不出去，只會留在 Prometheus 的 `ALERTS` 序列與
> Grafana 的「告警推播鏈路」面板上。接上外部管道（下方 runbook）後才有
> 真正的第二條腿。

`Watchdog` 是 **死人開關（dead man's switch）**：一條 `vector(1)` 永遠觸發的
告警，每 2 分鐘經由完整鏈路送到接收器一次。接收器把它記成
`tw_stock_alert_watchdog_last_timestamp_seconds`，超過 15 分鐘沒更新就代表
鏈路某處斷了。**把「沉默」轉成可觀測的訊號**，與
`ServiceMonitorCheckStalled` 是同一套思路。

> **殘留限制（誠實揭露）**：這套互看仍在同一台主機、同一個 Docker
> daemon 內。若整台主機或 Docker 全掛，沒有任何一段能對外發聲。要補這個缺口
> 必須有**外部**管道（下方 runbook 的 SMTP／Slack，或外部的 healthchecks.io
> 之類心跳服務）。在那之前，本地管道保證的是「監控自己壞掉時會留下紀錄且
> 可被查覺」，不是「一定會即時通知到人」。

#### 本地推播管道（預設，免憑證）

Service Monitor 行程內建 webhook 接收器（`src/alert_receiver.py`，port 9103）：

- 通知落地為 `logs/alerts/notifications-YYYYMMDD.jsonl`（JSON Lines，可稽核）。
- 同步寫入專案日誌：critical 走 `logger.error`、warning 走 `logger.warning`。
- 轉成 Prometheus 指標，可在 Grafana 上看推播是否正常：

| 指標 | 說明 |
|------|------|
| `tw_stock_alert_notifications_total{alertname,severity,status}` | 收到的通知數 |
| `tw_stock_alert_receiver_up` | 接收器是否服務中 |
| `tw_stock_alert_last_notification_timestamp_seconds` | 最近一次收到通知的時間 |
| `tw_stock_alert_watchdog_last_timestamp_seconds` | 最近一次心跳時間 |

> 落地檔按日切檔但**沒有自動清除**，長期會在具名 volume
> `tw-stock-server-monitor_logs` 內累積（心跳不落地，故量很小：只有真正的
> 告警才寫入）。需要時自行刪除舊檔即可。

```bash
# 查看今天送出的告警通知
docker exec tw-stock-server-monitor \
  cat /app/logs/alerts/notifications-$(date +%Y%m%d).jsonl

# 手動送一則測試通知（確認接收器活著）
docker exec tw-stock-server-monitor python - <<'PY'
import json, urllib.request
payload = {"status": "firing", "receiver": "manual-test", "alerts": [{
    "status": "firing",
    "labels": {"alertname": "ManualTest", "severity": "warning"},
    "annotations": {"summary": "手動測試通知"}}]}
req = urllib.request.Request(
    "http://127.0.0.1:9103/alerts",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
```

#### 設定外部推播管道（需使用者提供憑證）

本地管道證明鏈路是通的，但**通知不會主動找上你**。要真正推播到手機／信箱，
需接 Email 或 Slack。**憑證屬於使用者，不會也不該由開發流程自行填入或猜測**，
`docker/alertmanager/alertmanager.yml` 底部只留註解範本。

啟用步驟：

1. 準備下列設定值（依選用管道擇一）：

   | 管道 | 需要的設定 | 取得方式 |
   |------|-----------|---------|
   | Email | `smarthost`（SMTP 主機:埠，如 `smtp.gmail.com:587`） | 郵件服務商文件 |
   | Email | `auth_username`（寄件帳號） | 你的信箱帳號 |
   | Email | **應用程式專用密碼**（非登入密碼） | Gmail：帳戶 → 安全性 → 兩步驟驗證 → 應用程式密碼 |
   | Email | `from` / `to`（寄件人／收件人） | 自行決定 |
   | Slack | **Incoming Webhook URL** | Slack → Apps → Incoming Webhooks → Add to Slack |
   | Slack | `channel`（頻道名稱） | 自行決定 |

2. 把**密碼類**設定寫成檔案放進 `docker/alertmanager/secrets/`
   （該目錄內容已被 `.gitignore` 排除，**不會進版控**）：

   ```bash
   printf '%s' '<應用程式密碼>' > docker/alertmanager/secrets/smtp_password
   printf '%s' '<Slack Webhook URL>' > docker/alertmanager/secrets/slack_webhook_url
   chmod 600 docker/alertmanager/secrets/*

   # Linux 部署另需這一步：容器內以 nobody(65534) 執行，600 會讓它讀不到
   # 憑證而啟動失敗。macOS 的 Docker Desktop 會自動 remap 擁有者，不需要。
   sudo chown 65534 docker/alertmanager/secrets/*
   ```

   > **為何不是 `docker/.env`**：Alertmanager **不會展開設定檔中的環境變數**，
   > `.env` 只對 Docker Compose 本身有效。若把密碼寫成 inline 值，就等於把它
   > 提交進版控——因此設定檔一律用 `auth_password_file` / `api_url_file`
   > 引用容器內的 `/etc/alertmanager/secrets/`（compose 已掛載該目錄）。
   > `tests/test_alertmanager_config.py` 會擋下 inline 憑證。

3. 解除 `alertmanager.yml` 底部對應區塊的註解（非密碼欄位如 `to` / `from` /
   `smarthost` 可直接填），並在 route 加上該 receiver；
   建議搭配 `continue: true` 與本地管道併送，外部服務掛掉時仍留有本地紀錄。
4. 套用設定並實測：

   ```bash
   docker compose -f docker/docker-compose.yaml up -d alertmanager
   docker exec alertmanager amtool --alertmanager.url=http://localhost:9093 \
     alert add TestAlert severity=critical component=ci \
     summary="外部管道測試"
   ```

#### 端對端演練（實測告警真的會響）

**只寫設定不算數**，必須實測。`scripts/alert_drill.sh` 會在**隔離的 Docker
網路**中重現「runner 容器停掉」事故，跑完整條
Prometheus → Alertmanager → 接收器 鏈路：

```bash
bash scripts/alert_drill.sh          # 跑完自動清理
bash scripts/alert_drill.sh --keep   # 保留容器供人工檢視
```

演練會斷言：6 條下游告警**確實觸發**（證明指標到位）、在 Alertmanager 中
狀態為 `suppressed`（證明抑制生效）、最終**只有 1 則通知**送達本地管道，
且 Watchdog 心跳有送到。

> **演練刻意不去停真正的 `gitlab-runner`**。其他 repo 隨時可能打 tag 部署，
> 停 runner 會讓它們的 pipeline 卡在 pending。事故情境改以假指標端點重現，
> 用的卻是**未經修改的正式 Alertmanager 設定**與正式告警規則（僅把 `for:`
> 縮短），因此演練通過等於正式設定通過。

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
| `MONITOR_CONTAINERS` | `gitlab-runner,gitlab` | 監控存活的容器名稱（逗號分隔，空字串停用）。部署時另加 `alertmanager` |
| `ALERT_RECEIVER_PORT` | `9103` | 本地告警接收器的監聽埠 |
| `ALERT_LOG_DIR` | `logs/alerts` | 告警通知的落地目錄 |
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

### 套用新的告警規則或推播設定

規則檔與 Alertmanager 設定都以 bind mount 掛進容器
（`./prometheus/rules:/etc/prometheus/rules:ro`、
`./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro`）。
**CI 的 `deploy` job 只重啟 Service Monitor，不會動 Prometheus 或
Alertmanager**，因此改完設定後需手動讓它們重新載入。**首次導入時這一步是必要
的**：在執行之前 `/api/v1/rules` 會回 `{"groups":[]}`，等於一條告警都沒有——
正是本套規則要消滅的無聲狀態，務必實際確認載入結果：

```bash
# 首次新增掛載點／新增 alertmanager 服務時必須重建容器
docker compose -f docker/docker-compose.yaml up -d prometheus alertmanager

# 僅修改規則或設定內容時，重啟即可
docker restart prometheus alertmanager

# 確認規則已載入
docker exec prometheus wget -qO- http://localhost:9090/api/v1/rules | head

# 確認 Prometheus 真的連上 Alertmanager（activeAlertmanagers 不可為空）
docker exec prometheus wget -qO- http://localhost:9090/api/v1/alertmanagers

# 確認 Alertmanager 載入的是新設定
docker exec alertmanager amtool --alertmanager.url=http://localhost:9093 \
  config show
```

## 執行測試

### Python 單元測試

```bash
# 先建 image：DockerHub 上的 :latest 可能還沒有最新依賴（例如 requests），
# 直接拿舊 image 跑會噴 ModuleNotFoundError。
bash docker/build.sh

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

docker run --rm -v "$(pwd)":/work -w /work --entrypoint promtool \
  prom/prometheus:latest test rules \
  tests/prometheus/notification_alerts_test.yml
```

`tests/test_alert_rules.py` 另會檢查**每條告警都有對應的 promtool 測試**，
新增規則卻忘記補測試時會直接失敗。

### 告警推播設定測試

```bash
# 語法檢查
docker run --rm -v "$(pwd)":/work -w /work --entrypoint amtool \
  prom/alertmanager:latest check-config docker/alertmanager/alertmanager.yml

# 路由檢查（確認告警走到預期的 receiver）
docker run --rm -v "$(pwd)":/work -w /work --entrypoint amtool \
  prom/alertmanager:latest config routes test \
  --config.file=docker/alertmanager/alertmanager.yml \
  alertname=GitLabRunnerContainerDown severity=critical component=ci
```

`tests/test_alertmanager_config.py` 守住 `amtool` 驗不出來的語意問題（抑制規則
名稱打錯、心跳間隔大於停擺門檻、硬編憑證等）；
`scripts/alert_drill.sh` 則做端對端實測，見「告警推播管道 → 端對端演練」。

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
> 由本 repo build**；Prometheus / Alertmanager / Grafana / Node Exporter 皆為
> 上游官方映像，且 compose 內以相對路徑 bind 掛載設定檔。因此 `deploy`
> **只 build + 重啟 `tw-stock-server-monitor` 一顆容器**，**絕不**`docker rm`
> 或重啟 prometheus / alertmanager / grafana / node-exporter，也**不**執行
> `docker compose`、不動 launchd / macOS 原生 exporter。
>
> 換句話說，**改 `alertmanager.yml` 或告警規則不會被 CI 自動套用**，需依
> 「套用新的告警規則或推播設定」手動重啟那兩顆容器。
>
> **順序很重要：先手動起 Prometheus / Alertmanager，再打 tag。** Service
> Monitor 的 `MONITOR_CONTAINERS` 已納入 `alertmanager`，若先打 tag、
> Alertmanager 卻還沒起來，`CIContainerDown` 與 `AlertmanagerDown` 會立刻
> 觸發，而此時偏偏沒有任何管道送得出去：
>
> ```bash
> docker compose -f docker/docker-compose.yaml up -d prometheus alertmanager
> ```

部署細節：

- **image**：`nk7260ynpa/tw-stock-monitor`，同時打 `:vX.Y.Z`（不含 `v`）與
  `:latest` 兩個 tag。
- **容器**：`tw-stock-server-monitor`，`--restart=always`。
- **網路**：`--network db_network`，讓 Service Monitor 對其他 Tw_stock 服務做
  TCP 探測，並讓 Prometheus 以容器名 `tw-stock-server-monitor:9102` 在
  `db_network` 上抓取指標。
- **port**：`--expose 9102`（指標）與 `--expose 9103`（Alertmanager webhook
  接收器），僅容器內部暴露、不對外 publish，與 compose 一致。
- **掛載**：`/var/run/docker.sock:ro`，供容器存活監控查詢 `gitlab-runner`
  等不 publish port 的 CI 容器。
- **環境變數**：`MONITOR_METRICS_PORT=9102`、`MONITOR_CHECK_INTERVAL=30`、
  `MONITOR_CHECK_TIMEOUT=5`、`MONITOR_CONTAINERS`、`ALERT_RECEIVER_PORT=9103`、
  `ALERT_LOG_DIR`、`GITLAB_URL`、`GITLAB_GROUP_ID`、`GITLAB_CHECK_INTERVAL`，
  以及由 **masked CI/CD 變數** `MONITOR_GITLAB_TOKEN` 傳入的 `GITLAB_TOKEN`
  （權杖不寫在 `.gitlab-ci.yml`）。
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
