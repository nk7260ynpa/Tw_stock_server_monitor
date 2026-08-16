"""告警規則檔的結構與覆蓋率測試。

`promtool test rules` 負責驗證「規則在什麼條件下會觸發」，本測試則負責守住
另外兩件事，避免規則寫了卻沒人驗證：

1. 每條告警都具備 severity / component 標籤與 summary / description 說明。
2. 每條告警都至少被一個 promtool 測試檔明確指名（`alertname:`）。

因此新增告警而忘記補 promtool 測試時，這裡會直接失敗。
"""

import os
import unittest

import yaml

# repo 根目錄
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(_ROOT, "docker", "prometheus", "rules")
RULE_TESTS_DIR = os.path.join(_ROOT, "tests", "prometheus")

# 事故（gitlab-runner Exited(0) 四週無告警）必須被覆蓋的告警
REQUIRED_ALERTS = {
    "GitLabRunnerContainerDown",       # 容器層：Exit 0 也算掛掉
    "GitLabNoOnlineRunner",            # 註冊層：沒有 runner 可接 job
    "GitLabRunnerOffline",
    "GitLabRunnerNoContact",
    "GitLabJobsStuckNoMatchingRunner",  # 業務層：job 因無 runner 卡住
    "GitLabPipelineFailed",
    "GitLabTagNotDeployed",            # 業務層：tag 打了卻沒部署
}

VALID_SEVERITIES = {"critical", "warning", "info"}


def _load_yaml(path):
    """讀取 YAML 檔。

    Args:
        path: 檔案路徑。

    Returns:
        dict: 解析後的內容。
    """
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _rule_files():
    """列出所有告警規則檔。

    Returns:
        list: 規則檔的絕對路徑清單。
    """
    return sorted(
        os.path.join(RULES_DIR, name)
        for name in os.listdir(RULES_DIR)
        if name.endswith(".yml")
    )


def _iter_alerts():
    """逐一產出所有告警規則。

    Yields:
        tuple: (規則檔名, 告警 dict)。
    """
    for path in _rule_files():
        content = _load_yaml(path)
        for group in content.get("groups", []):
            for rule in group.get("rules", []):
                if "alert" in rule:
                    yield os.path.basename(path), rule


class TestAlertRuleStructure(unittest.TestCase):
    """測試告警規則的結構完整性。"""

    def test_rules_directory_is_not_empty(self):
        """規則目錄必須有規則檔。"""
        self.assertTrue(_rule_files(), "找不到任何告警規則檔")

    def test_every_alert_has_labels_and_annotations(self):
        """每條告警都要有 severity / component 與說明文字。"""
        for filename, rule in _iter_alerts():
            name = rule["alert"]
            with self.subTest(alert=name, file=filename):
                labels = rule.get("labels") or {}
                annotations = rule.get("annotations") or {}

                self.assertIn(labels.get("severity"), VALID_SEVERITIES)
                self.assertTrue(labels.get("component"), "缺少 component 標籤")
                self.assertTrue(annotations.get("summary"), "缺少 summary")
                self.assertTrue(
                    annotations.get("description"), "缺少 description"
                )
                self.assertTrue(rule.get("expr"), "缺少 expr")

    def test_alert_names_are_unique(self):
        """告警名稱不可重複，否則難以對應處理程序。"""
        names = [rule["alert"] for _, rule in _iter_alerts()]
        self.assertEqual(len(names), len(set(names)), "告警名稱重複")

    def test_stateful_alerts_have_for_duration(self):
        """除 absent() 類外，告警都應設 for，避免抖動誤報。"""
        for filename, rule in _iter_alerts():
            with self.subTest(alert=rule["alert"], file=filename):
                self.assertTrue(rule.get("for"), "缺少 for 持續時間")


class TestAlertCoverage(unittest.TestCase):
    """測試告警是否都有對應的 promtool 測試。"""

    def _tested_alert_names(self):
        """蒐集 promtool 測試檔中出現過的告警名稱。

        Returns:
            set: 告警名稱集合。
        """
        names = set()
        for filename in os.listdir(RULE_TESTS_DIR):
            if not filename.endswith(".yml"):
                continue
            content = _load_yaml(os.path.join(RULE_TESTS_DIR, filename))
            for case in content.get("tests", []):
                for item in case.get("alert_rule_test", []):
                    if item.get("alertname"):
                        names.add(item["alertname"])
        return names

    def test_required_incident_alerts_exist(self):
        """事故複盤指定的告警必須存在。"""
        defined = {rule["alert"] for _, rule in _iter_alerts()}
        missing = REQUIRED_ALERTS - defined
        self.assertFalse(missing, "缺少事故必要告警: {}".format(sorted(missing)))

    def test_every_alert_is_covered_by_rule_test(self):
        """每條告警都必須被 promtool 測試檔驗證過。"""
        defined = {rule["alert"] for _, rule in _iter_alerts()}
        uncovered = defined - self._tested_alert_names()
        self.assertFalse(
            uncovered,
            "以下告警沒有 promtool 測試: {}".format(sorted(uncovered)),
        )

    def test_rule_tests_reference_existing_rule_files(self):
        """promtool 測試檔引用的規則檔必須存在。"""
        for filename in os.listdir(RULE_TESTS_DIR):
            if not filename.endswith(".yml"):
                continue
            path = os.path.join(RULE_TESTS_DIR, filename)
            content = _load_yaml(path)
            for rule_ref in content.get("rule_files", []):
                resolved = os.path.normpath(
                    os.path.join(RULE_TESTS_DIR, rule_ref)
                )
                with self.subTest(test_file=filename, rule_file=rule_ref):
                    self.assertTrue(
                        os.path.exists(resolved),
                        "規則檔不存在: {}".format(resolved),
                    )


if __name__ == "__main__":
    unittest.main()
