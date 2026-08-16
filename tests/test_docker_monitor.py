"""Docker 容器狀態監控單元測試。

重點在於重現 2026-07/08 事故的訊號：容器 **Exit 0** 也必須被判定為異常。
"""

import http.client
import json
import unittest
from unittest.mock import MagicMock, patch

from src.docker_monitor import (
    CONTAINER_STATES,
    DEFAULT_CONTAINERS,
    DockerAPIError,
    DockerClient,
    _extract_state,
    _update_container_metrics,
    collect_container_health,
    get_monitored_containers,
)
from src.registry import registry


def _running_info(started_at="2026-08-16T00:28:44.381378677Z",
                  policy="unless-stopped"):
    """建立 running 狀態的 inspect 假資料。

    Args:
        started_at: 容器啟動時間字串。
        policy: restart policy 名稱。

    Returns:
        dict: 模擬的 Docker inspect 結果。
    """
    return {
        "State": {"Status": "running", "ExitCode": 0, "StartedAt": started_at},
        "HostConfig": {"RestartPolicy": {"Name": policy}},
    }


def _exited_info(exit_code=0):
    """建立 exited 狀態的 inspect 假資料。

    Args:
        exit_code: 結束碼，預設 0（本次事故的情境）。

    Returns:
        dict: 模擬的 Docker inspect 結果。
    """
    return {
        "State": {
            "Status": "exited",
            "ExitCode": exit_code,
            "StartedAt": "2026-07-18T03:00:00.000000000Z",
        },
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
    }


class TestExtractState(unittest.TestCase):
    """測試容器狀態正規化。"""

    def test_running(self):
        """running 應原樣回傳。"""
        self.assertEqual(_extract_state(_running_info()), "running")

    def test_exited(self):
        """exited 應原樣回傳。"""
        self.assertEqual(_extract_state(_exited_info()), "exited")

    def test_missing_container(self):
        """容器不存在（None）應回傳 missing。"""
        self.assertEqual(_extract_state(None), "missing")

    def test_unknown_status(self):
        """未知狀態值應回傳 unknown。"""
        info = {"State": {"Status": "zombie"}}
        self.assertEqual(_extract_state(info), "unknown")

    def test_status_absent(self):
        """缺少 State 欄位時應回傳 unknown。"""
        self.assertEqual(_extract_state({}), "unknown")


class TestUpdateContainerMetrics(unittest.TestCase):
    """測試容器指標更新。"""

    def test_exit_zero_is_treated_as_down(self):
        """Exit 0 的 exited 容器必須被視為異常（up=0）。"""
        _update_container_metrics("exit-zero-case", _exited_info(0))

        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_up", {"container": "exit-zero-case"}
            ),
            0,
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_state",
                {"container": "exit-zero-case", "state": "exited"},
            ),
            1,
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_exit_code", {"container": "exit-zero-case"}
            ),
            0,
        )

    def test_running_container(self):
        """running 容器應為 up=1 且僅 running 狀態為 1。"""
        _update_container_metrics("running-case", _running_info())

        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_up", {"container": "running-case"}
            ),
            1,
        )
        for state in CONTAINER_STATES:
            expected = 1 if state == "running" else 0
            self.assertEqual(
                registry.get_sample_value(
                    "tw_stock_container_state",
                    {"container": "running-case", "state": state},
                ),
                expected,
                msg="狀態 {} 的值不符預期".format(state),
            )

    def test_nanosecond_start_time_is_parsed(self):
        """奈秒精度的啟動時間應能解析成 epoch 秒。"""
        _update_container_metrics("timestamp-case", _running_info())

        value = registry.get_sample_value(
            "tw_stock_container_start_timestamp_seconds",
            {"container": "timestamp-case"},
        )
        self.assertGreater(value, 1_700_000_000)

    def test_restart_policy_metric(self):
        """restart policy 為 unless-stopped 時應為 0，always 時為 1。"""
        _update_container_metrics("policy-unless", _running_info())
        _update_container_metrics(
            "policy-always", _running_info(policy="always")
        )

        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_restart_policy_always",
                {"container": "policy-unless"},
            ),
            0,
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_restart_policy_always",
                {"container": "policy-always"},
            ),
            1,
        )

    def test_missing_container_metrics(self):
        """容器不存在時 up=0 且 missing 狀態為 1。"""
        _update_container_metrics("missing-case", None)

        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_up", {"container": "missing-case"}
            ),
            0,
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_state",
                {"container": "missing-case", "state": "missing"},
            ),
            1,
        )


class TestCollectContainerHealth(unittest.TestCase):
    """測試容器健康狀態收集流程。"""

    def test_collect_updates_metrics(self):
        """正常情況應更新指標並回報 API 可用。"""
        client = MagicMock()
        client.inspect_container.return_value = _exited_info(0)
        logger = MagicMock()

        result = collect_container_health(logger, client, ["collect-exited"])

        self.assertTrue(result)
        self.assertEqual(registry.get_sample_value("tw_stock_docker_api_up"), 1)
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_up", {"container": "collect-exited"}
            ),
            0,
        )
        logger.warning.assert_called()

    def test_all_queries_failed_keeps_previous_values(self):
        """Docker API 全掛時應只降 docker_api_up，不覆寫容器狀態。"""
        ok_client = MagicMock()
        ok_client.inspect_container.return_value = _running_info()
        logger = MagicMock()
        collect_container_health(logger, ok_client, ["keep-value-case"])

        failing_client = MagicMock()
        failing_client.inspect_container.side_effect = DockerAPIError("boom")
        result = collect_container_health(
            logger, failing_client, ["keep-value-case"]
        )

        self.assertFalse(result)
        self.assertEqual(registry.get_sample_value("tw_stock_docker_api_up"), 0)
        # 仍保留上一輪的 running，避免誤報成「容器全掛」
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_up", {"container": "keep-value-case"}
            ),
            1,
        )

    def test_partial_failure_still_updates_others(self):
        """部分容器查詢失敗時，其餘容器仍應更新。"""
        client = MagicMock()
        client.inspect_container.side_effect = [
            DockerAPIError("boom"),
            _running_info(),
        ]
        logger = MagicMock()

        result = collect_container_health(
            logger, client, ["partial-bad", "partial-good"]
        )

        self.assertTrue(result)
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_container_up", {"container": "partial-good"}
            ),
            1,
        )

    def test_empty_container_list(self):
        """未設定監控容器時不應呼叫 Docker API。"""
        client = MagicMock()
        logger = MagicMock()

        result = collect_container_health(logger, client, [])

        self.assertTrue(result)
        client.inspect_container.assert_not_called()


class TestGetMonitoredContainers(unittest.TestCase):
    """測試監控容器清單設定。"""

    def test_default_contains_gitlab_runner(self):
        """預設必須包含 gitlab-runner。"""
        self.assertIn("gitlab-runner", get_monitored_containers({}))
        self.assertEqual(list(DEFAULT_CONTAINERS), get_monitored_containers({}))

    def test_override_by_env(self):
        """環境變數應可覆寫清單並去除空白。"""
        result = get_monitored_containers({"MONITOR_CONTAINERS": "a, b ,c"})
        self.assertEqual(result, ["a", "b", "c"])

    def test_empty_env_disables(self):
        """設為空字串代表停用容器監控。"""
        self.assertEqual(get_monitored_containers({"MONITOR_CONTAINERS": ""}), [])


class TestDockerClient(unittest.TestCase):
    """測試 Docker Engine API 客戶端。"""

    def test_inspect_container_success(self):
        """200 回應應解析成 dict。"""
        client = DockerClient()
        payload = json.dumps(_running_info()).encode()
        with patch.object(client, "_get", return_value=(200, payload)):
            info = client.inspect_container("gitlab-runner")
        self.assertEqual(info["State"]["Status"], "running")

    def test_inspect_container_not_found(self):
        """404 應回傳 None（容器不存在）。"""
        client = DockerClient()
        with patch.object(client, "_get", return_value=(404, b"{}")):
            self.assertIsNone(client.inspect_container("ghost"))

    def test_inspect_container_unexpected_status(self):
        """非 200/404 應拋出 DockerAPIError。"""
        client = DockerClient()
        with patch.object(client, "_get", return_value=(500, b"boom")):
            with self.assertRaises(DockerAPIError):
                client.inspect_container("gitlab-runner")

    def test_inspect_container_invalid_json(self):
        """回應無法解析時應拋出 DockerAPIError。"""
        client = DockerClient()
        with patch.object(client, "_get", return_value=(200, b"not-json")):
            with self.assertRaises(DockerAPIError):
                client.inspect_container("gitlab-runner")

    @patch("src.docker_monitor._UnixSocketConnection")
    def test_get_wraps_connection_error(self, mock_conn_cls):
        """socket 無法連線時應轉成 DockerAPIError。"""
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("no such file")
        mock_conn_cls.return_value = mock_conn

        client = DockerClient()
        with self.assertRaises(DockerAPIError):
            client.inspect_container("gitlab-runner")
        mock_conn.close.assert_called_once()

    @patch("src.docker_monitor._UnixSocketConnection")
    def test_ping(self, mock_conn_cls):
        """ping 應依狀態碼回傳布林值。"""
        mock_conn = MagicMock()
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"OK"
        mock_conn.getresponse.return_value = response
        mock_conn_cls.return_value = mock_conn

        self.assertTrue(DockerClient().ping())

    @patch("src.docker_monitor.socket.socket")
    def test_unix_socket_connect(self, mock_socket_cls):
        """連線應建立 AF_UNIX socket 並連到指定路徑。"""
        from src.docker_monitor import _UnixSocketConnection

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        conn = _UnixSocketConnection("/var/run/docker.sock", timeout=3)
        conn.connect()

        mock_socket_cls.assert_called_once()
        mock_sock.connect.assert_called_once_with("/var/run/docker.sock")
        self.assertIs(conn.sock, mock_sock)

    @patch("src.docker_monitor._UnixSocketConnection")
    def test_get_wraps_http_exception(self, mock_conn_cls):
        """HTTP 協定錯誤也應轉成 DockerAPIError。"""
        mock_conn = MagicMock()
        mock_conn.getresponse.side_effect = http.client.HTTPException("bad")
        mock_conn_cls.return_value = mock_conn

        with self.assertRaises(DockerAPIError):
            DockerClient().inspect_container("gitlab-runner")


if __name__ == "__main__":
    unittest.main()
