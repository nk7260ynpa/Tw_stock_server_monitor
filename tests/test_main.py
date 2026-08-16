"""主程式單元測試。

測試服務健康檢查邏輯與 Prometheus 指標更新。
"""

import socket
import unittest
from unittest.mock import MagicMock, patch

from src.main import (
    MONITORED_SERVICES,
    check_service,
    collect_service_health,
    _signal_handler,
)


class TestCheckService(unittest.TestCase):
    """測試 check_service TCP 連線檢查函式。"""

    @patch("src.main.socket.socket")
    def test_service_up(self, mock_socket_cls):
        """服務可用時應回傳 (True, response_time)。"""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        is_up, response_time = check_service("localhost", 8080, timeout=2)

        self.assertTrue(is_up)
        self.assertGreaterEqual(response_time, 0)
        mock_sock.settimeout.assert_called_once_with(2)
        mock_sock.connect.assert_called_once_with(("localhost", 8080))
        mock_sock.close.assert_called_once()

    @patch("src.main.socket.socket")
    def test_service_down_connection_refused(self, mock_socket_cls):
        """服務不可用（連線被拒）時應回傳 (False, -1)。"""
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_cls.return_value = mock_sock

        is_up, response_time = check_service("localhost", 9999, timeout=2)

        self.assertFalse(is_up)
        self.assertEqual(response_time, -1)

    @patch("src.main.socket.socket")
    def test_service_down_timeout(self, mock_socket_cls):
        """服務不可用（逾時）時應回傳 (False, -1)。"""
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = socket.timeout("timed out")
        mock_socket_cls.return_value = mock_sock

        is_up, response_time = check_service("localhost", 9999, timeout=1)

        self.assertFalse(is_up)
        self.assertEqual(response_time, -1)

    @patch("src.main.socket.socket")
    def test_service_down_os_error(self, mock_socket_cls):
        """服務不可用（OS 錯誤）時應回傳 (False, -1)。"""
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = OSError("Network unreachable")
        mock_socket_cls.return_value = mock_sock

        is_up, response_time = check_service("nonexistent-host", 8080, timeout=1)

        self.assertFalse(is_up)
        self.assertEqual(response_time, -1)


class TestCollectServiceHealth(unittest.TestCase):
    """測試 collect_service_health 指標收集函式。"""

    @patch("src.main.check_service")
    @patch("src.main.service_response_time")
    @patch("src.main.service_up")
    def test_collect_updates_metrics_for_up_service(
        self, mock_up, mock_rt, mock_check
    ):
        """服務可用時應更新 up=1 與回應時間指標。"""
        mock_check.return_value = (True, 0.05)
        mock_up_labels = MagicMock()
        mock_up.labels.return_value = mock_up_labels
        mock_rt_labels = MagicMock()
        mock_rt.labels.return_value = mock_rt_labels

        logger = MagicMock()
        collect_service_health(logger, timeout=3)

        # 應對每個服務都呼叫 check_service
        self.assertEqual(mock_check.call_count, len(MONITORED_SERVICES))

        # 應設定 up=1
        mock_up_labels.set.assert_called_with(1)

        # 應設定回應時間
        mock_rt_labels.set.assert_called_with(0.05)

    @patch("src.main.check_service")
    @patch("src.main.service_response_time")
    @patch("src.main.service_up")
    def test_collect_updates_metrics_for_down_service(
        self, mock_up, mock_rt, mock_check
    ):
        """服務不可用時應更新 up=0 與回應時間=-1。"""
        mock_check.return_value = (False, -1)
        mock_up_labels = MagicMock()
        mock_up.labels.return_value = mock_up_labels
        mock_rt_labels = MagicMock()
        mock_rt.labels.return_value = mock_rt_labels

        logger = MagicMock()
        collect_service_health(logger, timeout=3)

        # 應設定 up=0
        mock_up_labels.set.assert_called_with(0)

        # 應設定回應時間為 -1
        mock_rt_labels.set.assert_called_with(-1)


class TestMonitoredServices(unittest.TestCase):
    """測試被監控服務清單的完整性。"""

    def test_all_services_have_required_fields(self):
        """每個被監控服務都應包含 name、host、port 欄位。"""
        for svc in MONITORED_SERVICES:
            self.assertIn("name", svc)
            self.assertIn("host", svc)
            self.assertIn("port", svc)
            self.assertIsInstance(svc["name"], str)
            self.assertIsInstance(svc["host"], str)
            self.assertIsInstance(svc["port"], int)

    def test_expected_services_count(self):
        """應監控 11 個服務。"""
        self.assertEqual(len(MONITORED_SERVICES), 11)

    def test_expected_service_names(self):
        """應包含所有預期的服務名稱。"""
        names = {svc["name"] for svc in MONITORED_SERVICES}
        expected = {
            "crawler", "mysql", "db_operating", "indicator",
            "ml", "tools", "dashboard", "webpage", "news",
            "hot", "specialinfo",
        }
        self.assertEqual(names, expected)

    def test_expected_ports(self):
        """應包含所有預期的端口（容器內服務端口）。"""
        ports = {svc["port"] for svc in MONITORED_SERVICES}
        # dashboard/webpage/tools 內部皆為 8000（外部映射另計）
        expected = {6738, 3306, 8080, 5001, 5002, 8000, 8003, 5050, 5055}
        self.assertEqual(ports, expected)


class TestSignalHandler(unittest.TestCase):
    """測試信號處理器。"""

    def test_signal_handler_sets_running_false(self):
        """信號處理器應將 _running 設為 False。"""
        import src.main as main_module
        main_module._running = True
        _signal_handler(None, None)
        self.assertFalse(main_module._running)
        # 還原
        main_module._running = True


class TestGitLabLoop(unittest.TestCase):
    """測試 GitLab 收集迴圈。

    GitLab 收集必須與主循環隔離：其 API 慢或逾時時，TCP 探測不能跟著停擺，
    否則服務真的掛掉也不會告警（Gauge 不會過期，會停在舊值）。
    """

    def test_loop_runs_until_stopped(self):
        """`_running` 轉為 False 後迴圈應結束。"""
        import src.main as main_module

        calls = []

        def fake_collect(logger, client, group_id, window_hours=None):
            calls.append(group_id)
            main_module._running = False
            return True

        main_module._running = True
        with patch("src.main.collect_gitlab_ci", side_effect=fake_collect):
            main_module.run_gitlab_loop(
                MagicMock(), MagicMock(), "38", 300, 24
            )

        self.assertEqual(calls, ["38"])
        main_module._running = True

    def test_loop_survives_unexpected_error(self):
        """收集拋出未預期例外時應記錄並繼續，不可讓執行緒死掉。"""
        import src.main as main_module

        logger = MagicMock()
        state = {"count": 0}

        def boom(*args, **kwargs):
            state["count"] += 1
            main_module._running = False
            raise RuntimeError("爆炸")

        main_module._running = True
        with patch("src.main.collect_gitlab_ci", side_effect=boom):
            main_module.run_gitlab_loop(logger, MagicMock(), "38", 1, 24)

        self.assertEqual(state["count"], 1)
        logger.exception.assert_called()
        main_module._running = True


if __name__ == "__main__":
    unittest.main()
