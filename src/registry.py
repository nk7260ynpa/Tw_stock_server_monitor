"""Service Monitor 共用的 Prometheus registry。

Service Monitor 由多個 collector 組成（服務 TCP 探測、Docker 容器狀態、
GitLab CI 基礎設施），全部指標都註冊到本模組的單一 registry，
再由 `src.main` 以 `start_http_server` 一次暴露。

獨立成模組是為了避免 `src.main` 與各 collector 模組互相 import 造成循環。
"""

from prometheus_client import CollectorRegistry

# 全域共用的指標 registry（不使用 prometheus_client 預設 registry，
# 避免混入 Python GC / process 等預設指標）。
registry = CollectorRegistry()
