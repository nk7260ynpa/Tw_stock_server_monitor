# 台股伺服器監控 (Tw Stock Server Monitor)

台股伺服器監控工具。

## 專案架構

```text
Tw_stock_server_monitor/
├── docker/
│   ├── build.sh                # 建立 Docker image 的執行腳本
│   ├── Dockerfile              # Docker image 定義
│   └── docker-compose.yaml     # Docker Compose 設定
├── logs/                       # 日誌檔案目錄
├── src/
│   ├── __init__.py
│   ├── logger.py               # 日誌設定模組
│   └── main.py                 # 主程式
├── tests/
│   ├── __init__.py
│   └── test_main.py            # 主程式單元測試
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt            # Python 套件依賴
└── run.sh                      # 啟動主程式的執行腳本
```

## 環境需求

- Docker

## 快速開始

### 1. 建立 Docker image

```bash
bash docker/build.sh
```

### 2. 啟動程式

```bash
bash run.sh
```

### 3. 使用 Docker Compose 啟動

```bash
docker compose -f docker/docker-compose.yaml up -d
```

## 執行測試

```bash
docker run --rm -v "$(pwd)":/app -w /app tw-stock-server-monitor:latest \
  python -m pytest tests/ -v
```

## 授權條款

詳見 [LICENSE](LICENSE) 檔案。
