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
        """應監控 7 個服務。"""
        self.assertEqual(len(MONITORED_SERVICES), 7)

    def test_expected_service_names(self):
        """應包含所有預期的服務名稱。"""
        names = {svc["name"] for svc in MONITORED_SERVICES}
        expected = {
            "crawler", "mysql", "db_operating", "indicator",
            "tools", "dashboard", "webpage",
        }
        self.assertEqual(names, expected)

    def test_expected_ports(self):
        """應包含所有預期的端口。"""
        ports = {svc["port"] for svc in MONITORED_SERVICES}
        expected = {6738, 3306, 8080, 5001, 8000, 8002, 7938}
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


if __name__ == "__main__":
    unittest.main()
