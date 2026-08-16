"""Alertmanager webhook 接收器測試。

重點在於「通知真的被收下、落地、並轉成指標」，以及接收器不會被畸形輸入
打掛——推播管道自己壞掉是最難察覺的失效。
"""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from src import alert_receiver
from src.alert_receiver import (
    alert_receiver_up,
    handle_payload,
    last_notification_timestamp,
    notifications_total,
    start_alert_receiver,
    watchdog_timestamp,
)


class _FakeLogger:
    """記錄呼叫內容的假 logger。"""

    def __init__(self):
        self.records = []

    def _record(self, level, msg, *args):
        self.records.append((level, msg % args if args else msg))

    def debug(self, msg, *args, **kwargs):
        self._record("debug", msg, *args)

    def info(self, msg, *args, **kwargs):
        self._record("info", msg, *args)

    def warning(self, msg, *args, **kwargs):
        self._record("warning", msg, *args)

    def error(self, msg, *args, **kwargs):
        self._record("error", msg, *args)

    def exception(self, msg, *args, **kwargs):
        self._record("exception", msg, *args)


def _counter_value(alertname, severity, status):
    """讀取通知計數器的目前值。

    Args:
        alertname: 告警名稱。
        severity: 嚴重度。
        status: firing / resolved。

    Returns:
        float: 計數值。
    """
    return notifications_total.labels(
        alertname=alertname, severity=severity, status=status
    )._value.get()


def _payload(alertname="GitLabRunnerContainerDown", severity="critical",
             status="firing", **extra):
    """組出一份 Alertmanager webhook 主體。

    Args:
        alertname: 告警名稱。
        severity: 嚴重度。
        status: firing / resolved。
        **extra: 要覆蓋的頂層欄位。

    Returns:
        dict: webhook 主體。
    """
    body = {
        "receiver": "local-webhook-critical",
        "status": status,
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": alertname,
                    "severity": severity,
                    "component": "ci",
                },
                "annotations": {
                    "summary": "GitLab Runner 容器未在執行，CI 已停擺",
                    "description": "測試用描述",
                },
                "startsAt": "2026-08-16T05:00:00.000Z",
            }
        ],
    }
    body.update(extra)
    return body


class TestHandlePayload(unittest.TestCase):
    """測試 webhook 主體處理邏輯。"""

    def setUp(self):
        self.log_dir = tempfile.mkdtemp(prefix="alert-test-")
        self.logger = _FakeLogger()

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _lines(self):
        """讀出落地檔的所有 JSON 記錄。

        Returns:
            list: 解析後的 dict 清單。
        """
        records = []
        for name in sorted(os.listdir(self.log_dir)):
            with open(os.path.join(self.log_dir, name), encoding="utf-8") as f:
                records.extend(json.loads(line) for line in f if line.strip())
        return records

    def test_firing_alert_is_recorded_and_counted(self):
        """firing 通知應落地成 JSONL 並累加計數器。"""
        before = _counter_value("GitLabRunnerContainerDown", "critical",
                                "firing")

        handled = handle_payload(_payload(), self.logger, self.log_dir)

        self.assertEqual(handled, 1)
        after = _counter_value("GitLabRunnerContainerDown", "critical",
                               "firing")
        self.assertEqual(after - before, 1)

        records = self._lines()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["alertname"], "GitLabRunnerContainerDown")
        self.assertEqual(records[0]["severity"], "critical")
        self.assertEqual(records[0]["component"], "ci")
        self.assertIn("CI 已停擺", records[0]["summary"])

    def test_critical_alert_logged_at_error_level(self):
        """critical 告警要用 error 等級寫 log，才不會淹沒在 warning 裡。"""
        handle_payload(_payload(), self.logger, self.log_dir)
        levels = [level for level, _ in self.logger.records]
        self.assertIn("error", levels)

    def test_resolved_alert_logged_at_info_level(self):
        """resolved 通知屬好消息，不應以 error 等級刷版面。"""
        handle_payload(_payload(status="resolved"), self.logger, self.log_dir)
        levels = [level for level, _ in self.logger.records]
        self.assertIn("info", levels)
        self.assertNotIn("error", levels)

    def test_watchdog_updates_heartbeat_but_is_not_written(self):
        """Watchdog 只更新心跳時間，不落地（每 2 分鐘一次會洗版）。"""
        watchdog_timestamp.set(0)

        handle_payload(
            _payload(alertname="Watchdog", severity="info"),
            self.logger, self.log_dir,
        )

        self.assertGreater(watchdog_timestamp._value.get(), 0)
        self.assertEqual(self._lines(), [])

    def test_last_notification_timestamp_updated(self):
        """任何通知都要更新「最後收到通知」時間。"""
        last_notification_timestamp.set(0)
        handle_payload(_payload(), self.logger, self.log_dir)
        self.assertGreater(last_notification_timestamp._value.get(), 0)

    def test_multiple_alerts_in_one_notification(self):
        """一則分組通知含多筆告警時要逐筆處理。"""
        body = _payload()
        body["alerts"].append({
            "status": "firing",
            "labels": {"alertname": "CIContainerDown", "severity": "warning",
                       "component": "ci"},
            "annotations": {"summary": "受監控容器 gitlab 未在執行"},
        })

        handled = handle_payload(body, self.logger, self.log_dir)

        self.assertEqual(handled, 2)
        names = {r["alertname"] for r in self._lines()}
        self.assertEqual(
            names, {"GitLabRunnerContainerDown", "CIContainerDown"}
        )

    def test_missing_labels_do_not_raise(self):
        """畸形通知不可讓接收器噴例外——通知管道自己壞掉最難察覺。"""
        body = {"status": "firing", "alerts": [{}, "not-a-dict", None]}
        handled = handle_payload(body, self.logger, self.log_dir)
        self.assertEqual(handled, 1)
        self.assertEqual(self._lines()[0]["alertname"], "unknown")

    def test_empty_payload_is_accepted(self):
        """空的 alerts 陣列應安靜地不做事。"""
        self.assertEqual(handle_payload({}, self.logger, self.log_dir), 0)
        self.assertEqual(self._lines(), [])

    def test_alert_status_overrides_payload_status(self):
        """單筆告警的 status 優先於整份通知的 status。"""
        body = _payload(status="firing")
        body["alerts"][0]["status"] = "resolved"
        handle_payload(body, self.logger, self.log_dir)
        self.assertEqual(self._lines()[0]["status"], "resolved")


class TestReceiverServer(unittest.TestCase):
    """以真實 HTTP 請求測試接收器（端到端最小驗證）。"""

    @classmethod
    def setUpClass(cls):
        cls.log_dir = tempfile.mkdtemp(prefix="alert-http-")
        cls.logger = _FakeLogger()
        # 埠 0 讓作業系統挑一個空閒埠，避免測試環境衝突
        cls.server = start_alert_receiver(cls.logger, port=0,
                                          log_dir=cls.log_dir)
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.log_dir, ignore_errors=True)

    def _post(self, body, path="/alerts"):
        """對接收器發 POST。

        Args:
            body: 要送出的 bytes。
            path: 請求路徑。

        Returns:
            int: HTTP 狀態碼。
        """
        req = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(self.port, path),
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_receiver_up_metric_is_set(self):
        """啟動成功時 tw_stock_alert_receiver_up 應為 1。"""
        self.assertEqual(alert_receiver_up._value.get(), 1)

    def test_healthz(self):
        """健康檢查端點供部署後 smoke test 使用。"""
        url = "http://127.0.0.1:{}/healthz".format(self.port)
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"ok")

    def test_post_notification(self):
        """實際 POST 一則通知應回 200 並落地。"""
        payload = _payload(alertname="GitLabTagNotDeployed")
        status = self._post(json.dumps(payload).encode("utf-8"))
        self.assertEqual(status, 200)

        deadline = time.time() + 5
        found = False
        while time.time() < deadline and not found:
            for name in os.listdir(self.log_dir):
                path = os.path.join(self.log_dir, name)
                with open(path, encoding="utf-8") as handle:
                    found = "GitLabTagNotDeployed" in handle.read()
            if not found:
                time.sleep(0.1)
        self.assertTrue(found, "通知未落地")

    def test_invalid_json_returns_400(self):
        """壞掉的 JSON 應回 400 而非讓連線中斷。"""
        self.assertEqual(self._post(b"{not json"), 400)

    def test_oversized_body_rejected(self):
        """超大主體應被擋下，避免記憶體被撐爆。"""
        req = urllib.request.Request(
            "http://127.0.0.1:{}/alerts".format(self.port),
            data=b"{}", method="POST",
            headers={"Content-Length": str(alert_receiver.MAX_BODY_BYTES + 1)},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        self.assertEqual(code, 413)

    def test_unknown_get_path_returns_404(self):
        """未知路徑回 404。"""
        url = "http://127.0.0.1:{}/nope".format(self.port)
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        self.assertEqual(code, 404)


class TestHeartbeatInitialisation(unittest.TestCase):
    """心跳 Gauge 的初始值（v1.2.0 踩過的坑，需防迴歸）。"""

    def test_heartbeat_initialised_at_startup(self):
        """啟動時心跳須設為當下時間，不可留 Gauge 預設值 0。

        留 0 會讓 `time() - 0` 變成天文數字，`AlertDeliveryStalled` 在開機
        瞬間就永久誤報；設成啟動時間則「開機後始終收不到心跳」仍抓得到。
        """
        watchdog_timestamp.set(0)
        started_at = time.time()

        server = start_alert_receiver(_FakeLogger(), port=0, log_dir=None)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        value = watchdog_timestamp._value.get()
        self.assertGreaterEqual(value, started_at)
        self.assertLessEqual(value, time.time())


class TestWriteNotificationFailure(unittest.TestCase):
    """落地失敗時的可觀測性。"""

    def test_write_failure_is_logged_not_silent(self):
        """寫不進帳本本身要被看見；靜默吞掉等於帳本悄悄斷檔。"""
        logger = _FakeLogger()
        # 用一個「存在但不是目錄」的路徑逼出 OSError
        handle, path = tempfile.mkstemp(prefix="alert-notadir-")
        os.close(handle)
        self.addCleanup(os.remove, path)

        result = alert_receiver._write_notification({"a": 1}, path, logger)

        self.assertIsNone(result)
        self.assertIn("exception", [level for level, _ in logger.records])


class TestStartFailure(unittest.TestCase):
    """啟動失敗的處理。"""

    def test_port_conflict_sets_metric_to_zero(self):
        """埠被占用時應設 receiver_up=0 而非直接讓行程崩潰。"""
        logger = _FakeLogger()
        first = start_alert_receiver(logger, port=0, log_dir=None)
        self.addCleanup(first.server_close)
        self.addCleanup(first.shutdown)
        port = first.server_address[1]

        second = start_alert_receiver(logger, port=port, log_dir=None)

        self.assertIsNone(second)
        self.assertEqual(alert_receiver_up._value.get(), 0)
        # 還原，避免影響其他測試
        alert_receiver_up.set(1)


if __name__ == "__main__":
    unittest.main()
