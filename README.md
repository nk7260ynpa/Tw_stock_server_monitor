# 台股伺服器監控 (Tw Stock Server Monitor)

台股伺服器監控工具，內建 Grafana + Prometheus 監控系統，
即時監控主機 CPU、記憶體、磁碟、網路等資源使用量，
並持續檢查各 Tw_stock 微服務的健康狀態。

支援 **macOS** 與 **Linux** 兩種部署環境。

## 專案架構

```text
Tw_stock_server_monitor/
├── docker/
│   ├── build.sh                          # 建立 Docker image 的執行腳本
│   ├── Dockerfile                        # Docker image 定義
│   ├── docker-compose.yaml               # Docker Compose 設定（含監控服務）
│   ├── prometheus/
│   │   └── prometheus.yml                # Prometheus 設定檔
│   └── grafana/
│       └── provisioning/
│           ├── datasources/
│           │   └── datasource.yml        # Grafana 資料來源設定
│           └── dashboards/
│               ├── dashboard.yml         # Grafana 儀表板 provider 設定
│               └── node-exporter.json    # 主機資源監控儀表板
├── logs/                                 # 日誌檔案目錄
├── src/
│   ├── __init__.py
│   ├── logger.py                         # 日誌設定模組
│   ├── macos_exporter.py                 # macOS 主機指標 Exporter
│   └── main.py                           # 主程式
├── tests/
│   ├── __init__.py
│   ├── test_macos_exporter.py            # macOS Exporter 單元測試
│   └── test_main.py                      # 主程式單元測試
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt                      # Python 套件依賴
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

## 環境需求

- Docker
- Docker Compose
- macOS 環境額外需要：Python 3、pip（安裝 psutil 與 prometheus_client）

## 快速開始

### 1. 啟動所有服務

```bash
bash run.sh
```

在 macOS 上執行時，會自動偵測並啟動 macOS Exporter。

### 2. 開啟 Grafana 儀表板

瀏覽器開啟 <http://localhost:3000>

- 帳號：`admin`
- 密碼：`admin`

進入後點選左側選單 **Dashboards**，即可看到自動載入的「主機資源監控」儀表板。

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
- **記憶體**：使用量、細項分類（應用程式/Buffers/Cached/Free）、Swap 使用量
- **磁碟**：各分區使用率、I/O 讀寫速率
- **網路**：流量趨勢、封包速率

### 服務健康檢查

Service Monitor 持續檢查以下 Tw_stock 微服務的 TCP 連線狀態：

| 服務 | 容器名稱 | 端口 |
|------|---------|------|
| Crawler | tw_stock_crawler | 6738 |
| MySQL | tw_stock_database | 3306 |
| DB Operating | tw_stock_db_operating | 8080 |
| Indicator | tw-stock-indicator | 5001 |
| Tools | tw_stock_tools | 8000 |
| Dashboard | tw_stock_dashboard | 8002 |
| Webpage | tw-stock-webpage | 7938 |
| News | tw_stock_news | 8003 |

暴露的 Prometheus 指標：

- `tw_stock_service_up`：服務健康狀態（1=正常, 0=異常）
- `tw_stock_service_response_time_seconds`：TCP 連線回應時間（秒）

## 其他操作

### 僅建立主程式 Docker image

```bash
bash docker/build.sh
```

### 單獨啟動 macOS Exporter

```bash
bash run_macos_exporter.sh
```

### 停止所有服務

```bash
docker compose -f docker/docker-compose.yaml down
# macOS 環境另需停止 exporter
pkill -f "src.macos_exporter" 2>/dev/null || true
```

### 停止並清除資料

```bash
docker compose -f docker/docker-compose.yaml down -v
```

## 執行測試

```bash
docker run --rm -v "$(pwd)":/app -w /app tw-stock-server-monitor:latest \
  python -m pytest tests/ -v
```

## 授權條款

詳見 [LICENSE](LICENSE) 檔案。
