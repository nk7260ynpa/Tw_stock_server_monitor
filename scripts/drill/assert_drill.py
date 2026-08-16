"""告警演練的驗收腳本。

在演練用的隔離網路中執行，向三個端點取證並判定通過與否：

* Prometheus `/api/v1/alerts`：確認事故指標**真的**讓那些規則觸發了
  （不是靠設定看起來對）。
* Alertmanager `/api/v2/alerts`：確認下游告警的狀態是 `suppressed`
  且 `inhibitedBy` 指向 runner 容器告警。
* 接收器 `/metrics` 與落地的 JSON Lines：確認最終**只送出一則**通知，
  以及 Watchdog 心跳有送達。

判定重點是「一次事故只響一次」。抑制沒生效時，設定檔語法照樣正確、
規則測試照樣通過，只有這裡會抓到。
"""

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROMETHEUS_URL = os.environ.get("DRILL_PROMETHEUS_URL",
                                "http://prometheus:9090")
ALERTMANAGER_URL = os.environ.get("DRILL_ALERTMANAGER_URL",
                                  "http://alertmanager:9093")
RECEIVER_URL = os.environ.get("DRILL_RECEIVER_URL",
                              "http://tw-stock-server-monitor:9199")
NOTIFICATION_DIR = os.environ.get("DRILL_NOTIFICATION_DIR", "/drill/out")
TIMEOUT_SECONDS = int(os.environ.get("DRILL_TIMEOUT", "240"))

# 事故本體：唯一應該送達的告警
EXPECTED_DELIVERED = "GitLabRunnerContainerDown"

# 這些是 runner 容器掛掉後必然連帶成立的下游告警。
# 它們必須「有觸發」但「不送出」——這正是抑制規則要證明的事。
EXPECTED_INHIBITED = [
    "GitLabNoOnlineRunner",
    "GitLabRunnerOffline",
    "GitLabRunnerNoContact",
    "GitLabJobsStuckNoMatchingRunner",
    "GitLabPipelineFailed",
    "GitLabTagNotDeployed",
]

WATCHDOG_ALERTNAME = "Watchdog"


def _get_json(url):
    """取得 JSON 回應。

    Args:
        url: 目標網址。

    Returns:
        dict: 解析後內容；失敗時為 None。
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def _get_text(url):
    """取得純文字回應。

    Args:
        url: 目標網址。

    Returns:
        str: 回應內容；失敗時為空字串。
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, OSError):
        return ""


def firing_alertnames():
    """取得 Prometheus 目前 firing 的告警名稱。

    Returns:
        set: 告警名稱集合。
    """
    data = _get_json("{}/api/v1/alerts".format(PROMETHEUS_URL)) or {}
    alerts = (data.get("data") or {}).get("alerts") or []
    return {
        alert["labels"]["alertname"]
        for alert in alerts
        if alert.get("state") == "firing" and alert.get("labels")
    }


def alertmanager_states():
    """取得 Alertmanager 中每個告警的狀態。

    Returns:
        dict: {告警名稱: (state, inhibitedBy 清單)}。
    """
    alerts = _get_json("{}/api/v2/alerts".format(ALERTMANAGER_URL)) or []
    states = {}
    for alert in alerts:
        name = (alert.get("labels") or {}).get("alertname")
        status = alert.get("status") or {}
        if name:
            states[name] = (status.get("state"), status.get("inhibitedBy")
                            or [])
    return states


def delivered_notifications():
    """讀取接收器落地的通知紀錄。

    Returns:
        list: 每則通知的 dict。
    """
    records = []
    pattern = os.path.join(NOTIFICATION_DIR, "notifications-*.jsonl")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def watchdog_count():
    """從接收器指標取出 Watchdog 心跳次數。

    Returns:
        float: 心跳次數；取不到時為 0.0。
    """
    for line in _get_text("{}/metrics".format(RECEIVER_URL)).splitlines():
        if line.startswith("tw_stock_alert_notifications_total{") and \
                'alertname="{}"'.format(WATCHDOG_ALERTNAME) in line:
            try:
                return float(line.rsplit(" ", 1)[1])
            except (IndexError, ValueError):
                return 0.0
    return 0.0


def wait_for_signals():
    """等待演練訊號齊備（下游告警觸發 + 通知送達 + 心跳送達）。

    Returns:
        bool: 是否在時限內全部齊備。
    """
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        firing = firing_alertnames()
        ready = (
            EXPECTED_DELIVERED in firing
            and all(name in firing for name in EXPECTED_INHIBITED)
            and delivered_notifications()
            and watchdog_count() >= 1
        )
        if ready:
            # 再多等一個分組週期，確保「被抑制的告警不會晚一步才送出」
            time.sleep(30)
            return True
        print("  ...等待中，目前 firing：{}".format(sorted(firing)))
        time.sleep(10)
    return False


def main():
    """執行驗收並回傳退出碼。

    Returns:
        int: 0 表示通過。
    """
    print("== 告警演練驗收 ==")
    ready = wait_for_signals()

    firing = firing_alertnames()
    states = alertmanager_states()
    notifications = delivered_notifications()
    heartbeats = watchdog_count()

    print("\n[1] Prometheus 觸發中的告警（共 {} 條）".format(len(firing)))
    for name in sorted(firing):
        print("    - {}".format(name))

    print("\n[2] Alertmanager 中的告警狀態")
    for name in sorted(states):
        state, inhibited_by = states[name]
        mark = "抑制" if state == "suppressed" else "送出"
        print("    - {:<34} {:<10} ({})".format(name, state, mark))

    print("\n[3] 實際送達本地推播管道的通知（{} 則）".format(
        len(notifications)))
    for record in notifications:
        print("    - [{}] {} / {} / {}".format(
            record.get("received_at"), record.get("alertname"),
            record.get("severity"), record.get("summary")))

    print("\n[4] Watchdog 心跳送達次數：{:.0f}".format(heartbeats))

    failures = []
    if not ready:
        failures.append("等待逾時：演練訊號未在 {} 秒內齊備".format(
            TIMEOUT_SECONDS))

    for name in [EXPECTED_DELIVERED] + EXPECTED_INHIBITED:
        if name not in firing:
            failures.append("規則未觸發：{}（事故指標沒讓它成立）".format(name))

    for name in EXPECTED_INHIBITED:
        state, inhibited_by = states.get(name, (None, []))
        if state != "suppressed":
            failures.append(
                "抑制失效：{} 狀態為 {}，應為 suppressed".format(name, state))
        elif not inhibited_by:
            failures.append("抑制來源不明：{} 沒有 inhibitedBy".format(name))

    main_state = states.get(EXPECTED_DELIVERED, (None, []))[0]
    if main_state != "active":
        failures.append("主告警 {} 狀態為 {}，應為 active".format(
            EXPECTED_DELIVERED, main_state))

    delivered_names = {record.get("alertname") for record in notifications}
    if delivered_names != {EXPECTED_DELIVERED}:
        failures.append("送達的告警不只事故本體：{}".format(
            sorted(delivered_names)))
    if len(notifications) != 1:
        failures.append(
            "同一次事故送出 {} 則通知，應只有 1 則".format(len(notifications)))
    if heartbeats < 1:
        failures.append("Watchdog 心跳未送達，死人開關失效")

    print("\n== 判定 ==")
    if failures:
        for failure in failures:
            print("  FAIL: {}".format(failure))
        return 1

    print("  PASS: 一次 runner 停擺事故 → 只送出 1 則通知"
          "（{} 則下游告警被抑制），心跳鏈路正常。".format(
              len(EXPECTED_INHIBITED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
