"""Alertmanager 設定檔的結構測試。

`amtool check-config` 只驗證語法正確，驗不出「語意上是錯的」——而抑制規則
最典型的失效就是**安靜地不生效**：把告警名稱打錯一個字，設定照樣通過檢查，
抑制卻永遠不會發生，於是一次事故仍舊炸出五封通知，然後使用者關掉通知。

因此這裡守住幾件 amtool 看不出來的事：

1. 抑制規則裡提到的每個告警名稱，都真的存在於規則檔中（防打錯字）。
2. 事故要求的因果抑制（runner 容器掛掉 → 抑制下游）確實存在。
3. Watchdog 心跳的重送間隔遠小於 `AlertDeliveryStalled` 的門檻，
   否則正常狀況也會被判成鏈路停擺。
4. 設定檔內沒有硬編憑證。
"""

import os
import re
import unittest

import yaml

# repo 根目錄
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTMANAGER_CONFIG = os.path.join(
    _ROOT, "docker", "alertmanager", "alertmanager.yml"
)
RULES_DIR = os.path.join(_ROOT, "docker", "prometheus", "rules")

# 事故要求：runner 容器掛掉時，這些下游告警必須被抑制
RUNNER_DOWN_INHIBITED = {
    "GitLabNoOnlineRunner",
    "GitLabRunnerOffline",
    "GitLabRunnerNoContact",
    "GitLabJobsStuckNoMatchingRunner",
    "GitLabTagNotDeployed",
}

# 心跳告警名稱，須與 src/alert_receiver.py 一致
WATCHDOG_ALERTNAME = "Watchdog"

_ALERTNAME_PATTERN = re.compile(r'alertname\s*=~?\s*"([^"]+)"')
_DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _load_yaml(path):
    """讀取 YAML 檔。

    Args:
        path: 檔案路徑。

    Returns:
        dict: 解析後的內容。
    """
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _parse_duration(value):
    """把 Alertmanager 的時間字串轉成秒。

    Args:
        value: 例如 "2m"、"15s"、"4h"。

    Returns:
        int: 秒數；無法解析時為 None。
    """
    match = _DURATION_PATTERN.match(str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * _DURATION_UNITS[match.group(2)]


def _matcher_alertnames(matchers):
    """從 matcher 字串清單取出所有告警名稱。

    支援 `alertname = "X"` 與 `alertname =~ "A|B|C"` 兩種寫法。

    Args:
        matchers: matcher 字串清單。

    Returns:
        set: 告警名稱集合。
    """
    names = set()
    for matcher in matchers or []:
        found = _ALERTNAME_PATTERN.search(str(matcher))
        if found:
            names.update(found.group(1).split("|"))
    return names


def _defined_alertnames():
    """蒐集所有規則檔中定義的告警名稱。

    Returns:
        set: 告警名稱集合。
    """
    names = set()
    for filename in os.listdir(RULES_DIR):
        if not filename.endswith(".yml"):
            continue
        content = _load_yaml(os.path.join(RULES_DIR, filename)) or {}
        for group in content.get("groups", []):
            for rule in group.get("rules", []):
                if "alert" in rule:
                    names.add(rule["alert"])
    return names


def _iter_routes(route):
    """深度走訪路由樹。

    Args:
        route: 根路由 dict。

    Yields:
        dict: 每一個路由節點。
    """
    if not route:
        return
    yield route
    for child in route.get("routes", []) or []:
        for node in _iter_routes(child):
            yield node


class TestAlertmanagerConfig(unittest.TestCase):
    """Alertmanager 設定檔結構測試。"""

    @classmethod
    def setUpClass(cls):
        cls.config = _load_yaml(ALERTMANAGER_CONFIG)
        cls.alertnames = _defined_alertnames()

    def test_receivers_referenced_by_routes_exist(self):
        """路由指到的 receiver 必須真的定義過，否則通知直接掉地上。"""
        defined = {r["name"] for r in self.config.get("receivers", [])}
        for route in _iter_routes(self.config.get("route")):
            with self.subTest(receiver=route.get("receiver")):
                if route.get("receiver"):
                    self.assertIn(route["receiver"], defined)

    def test_every_receiver_has_a_delivery_target(self):
        """每個 receiver 都要有實際的送達設定，不可是空殼。"""
        for receiver in self.config.get("receivers", []):
            with self.subTest(receiver=receiver["name"]):
                targets = [
                    key for key in receiver
                    if key.endswith("_configs") and receiver[key]
                ]
                self.assertTrue(
                    targets, "receiver {} 沒有任何送達設定".format(
                        receiver["name"]
                    )
                )

    def test_root_route_has_grouping(self):
        """根路由必須設定分組，否則 18 條規則會各發一封。"""
        route = self.config["route"]
        self.assertTrue(route.get("group_by"))
        for key in ("group_wait", "group_interval", "repeat_interval"):
            with self.subTest(key=key):
                self.assertIsNotNone(_parse_duration(route.get(key)))

    def test_inhibit_rules_reference_existing_alerts(self):
        """抑制規則裡的告警名稱必須存在——打錯字會安靜地讓抑制失效。"""
        for rule in self.config.get("inhibit_rules", []):
            names = (_matcher_alertnames(rule.get("source_matchers"))
                     | _matcher_alertnames(rule.get("target_matchers")))
            for name in names:
                with self.subTest(alertname=name):
                    self.assertIn(
                        name, self.alertnames,
                        "抑制規則提到不存在的告警 {}".format(name),
                    )

    def test_runner_down_inhibits_downstream_alerts(self):
        """事故要求：runner 容器掛掉必須抑制下游連帶告警。"""
        inhibited = set()
        for rule in self.config.get("inhibit_rules", []):
            sources = _matcher_alertnames(rule.get("source_matchers"))
            if "GitLabRunnerContainerDown" in sources:
                inhibited |= _matcher_alertnames(rule.get("target_matchers"))

        missing = RUNNER_DOWN_INHIBITED - inhibited
        self.assertFalse(
            missing,
            "runner 容器掛掉時未抑制下游告警：{}".format(sorted(missing)),
        )

    def test_alert_is_not_self_inhibiting(self):
        """告警不可抑制自己，否則它永遠不會送出。"""
        for rule in self.config.get("inhibit_rules", []):
            sources = _matcher_alertnames(rule.get("source_matchers"))
            targets = _matcher_alertnames(rule.get("target_matchers"))
            overlap = sources & targets
            with self.subTest(sources=sorted(sources)):
                self.assertFalse(
                    overlap, "告警抑制了自己：{}".format(sorted(overlap))
                )

    def test_watchdog_has_dedicated_route(self):
        """Watchdog 必須有自己的路由，且不能被 4 小時的預設間隔拖慢。"""
        watchdog_routes = [
            route for route in _iter_routes(self.config.get("route"))
            if WATCHDOG_ALERTNAME in _matcher_alertnames(
                route.get("matchers")
            )
        ]
        self.assertEqual(len(watchdog_routes), 1)
        self.assertIsNotNone(
            _parse_duration(watchdog_routes[0].get("repeat_interval"))
        )

    def test_watchdog_heartbeat_faster_than_stall_threshold(self):
        """心跳間隔必須遠小於 AlertDeliveryStalled 門檻，否則正常也會誤報。

        這是兩個檔案之間的隱性契約：把 repeat_interval 調到門檻以上，
        推播鏈路明明健康卻會天天噴 critical，最後就是有人把告警靜音。
        """
        watchdog_routes = [
            route for route in _iter_routes(self.config.get("route"))
            if WATCHDOG_ALERTNAME in _matcher_alertnames(
                route.get("matchers")
            )
        ]
        interval = _parse_duration(watchdog_routes[0]["repeat_interval"])

        threshold = self._stall_threshold_seconds()
        self.assertIsNotNone(threshold, "找不到 AlertDeliveryStalled 門檻")
        # 至少要能連錯 3 次心跳才判定停擺，避免單次抖動就告警
        self.assertLessEqual(interval * 3, threshold)

    def _stall_threshold_seconds(self):
        """從規則檔取出 AlertDeliveryStalled 的秒數門檻。

        Returns:
            int: 門檻秒數；找不到時為 None。
        """
        path = os.path.join(RULES_DIR, "notification_alerts.yml")
        content = _load_yaml(path) or {}
        for group in content.get("groups", []):
            for rule in group.get("rules", []):
                if rule.get("alert") != "AlertDeliveryStalled":
                    continue
                found = re.search(
                    r"tw_stock_alert_watchdog_last_timestamp_seconds\s*>\s*"
                    r"(\d+)",
                    rule.get("expr", ""),
                )
                if found:
                    return int(found.group(1))
        return None

    def test_no_hardcoded_credentials(self):
        """設定檔不得含硬編憑證；外部管道範本必須維持註解狀態。"""
        with open(ALERTMANAGER_CONFIG, encoding="utf-8") as handle:
            active_lines = [
                line for line in handle
                if line.strip() and not line.strip().startswith("#")
            ]
        joined = "".join(active_lines)
        for forbidden in ("auth_password", "api_url", "smarthost",
                          "auth_username"):
            with self.subTest(keyword=forbidden):
                self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
