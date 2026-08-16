"""GitLab CI 基礎設施監控單元測試。

重點在於重現 2026-07/08 事故的訊號：runner 離線、job 因無可用 runner 卡住
（`stuck_pending_no_matching_runners`）、以及 tag 打了卻沒成功部署。
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, mock_open, patch

import requests

from src.gitlab_monitor import (
    STUCK_FAILURE_REASON,
    GitLabAPIError,
    GitLabClient,
    collect_gitlab_ci,
    fetch_project_state,
    fetch_runner_states,
    resolve_token,
)
from src.registry import registry

# 測試用固定時間（Unix epoch 秒）
NOW = 1_786_850_000.0
GROUP_ID = "38"


def _iso(epoch_seconds):
    """把 epoch 秒數轉成 GitLab 風格的 ISO 8601 字串。

    Args:
        epoch_seconds: Unix epoch 秒數。

    Returns:
        str: 形如 `2026-08-16T02:05:34.533Z` 的字串。
    """
    moment = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FakeGitLabClient:
    """以字典驅動的假 GitLab 客戶端。"""

    def __init__(self, responses, failing_paths=None):
        """初始化假客戶端。

        Args:
            responses: path → 回應內容的字典。
            failing_paths: 需要拋出 GitLabAPIError 的 path 集合。
        """
        self.responses = responses
        self.failing_paths = set(failing_paths or ())
        self.calls = []

    def get(self, path, params=None):
        """回傳預先準備的假資料。

        Args:
            path: API 路徑。
            params: query 參數。

        Returns:
            預先準備的回應內容。

        Raises:
            GitLabAPIError: path 在 failing_paths 中。
        """
        self.calls.append((path, params))
        if path in self.failing_paths:
            raise GitLabAPIError("模擬失敗: {}".format(path))
        if params and params.get("ref"):
            keyed = "{}?ref={}".format(path, params["ref"])
            if keyed in self.responses:
                return self.responses[keyed]
        return self.responses.get(path, [])


def _build_responses(pipeline_status="success", tag="v2.15.0",
                     tag_status="success", failed_jobs=None,
                     runner_online=True):
    """組出一份完整的假 API 回應。

    Args:
        pipeline_status: 最新 pipeline 狀態。
        tag: 最新 tag 名稱。
        tag_status: 該 tag 對應 pipeline 的狀態。
        failed_jobs: 失敗 job 清單，None 代表沒有失敗 job。
        runner_online: runner 是否 online。

    Returns:
        dict: 供 `FakeGitLabClient` 使用的回應表。
    """
    return {
        "/groups/{}/runners".format(GROUP_ID): [
            {
                "id": 1,
                "description": "All-project-runner",
                "online": runner_online,
                "paused": False,
            }
        ],
        "/runners/1": {"contacted_at": _iso(NOW - 300)},
        "/groups/{}/projects".format(GROUP_ID): [
            {"id": 9, "path": "Tw_stock_DB_Operating"}
        ],
        "/projects/9/pipelines": [
            {
                "id": 145,
                "ref": tag,
                "status": pipeline_status,
                "created_at": _iso(NOW - 1200),
            }
        ],
        "/projects/9/pipelines?ref={}".format(tag): [
            {"id": 145, "status": tag_status, "created_at": _iso(NOW - 1200)}
        ],
        "/projects/9/jobs": failed_jobs or [],
        "/projects/9/repository/tags": [
            {"name": tag, "created_at": _iso(NOW - 7200),
             "commit": {"created_at": _iso(NOW - 7300)}}
        ],
    }


class TestResolveToken(unittest.TestCase):
    """測試權杖解析。"""

    def test_env_token(self):
        """應優先使用 GITLAB_TOKEN。"""
        self.assertEqual(resolve_token({"GITLAB_TOKEN": " abc "}), "abc")

    def test_token_file(self):
        """未設 GITLAB_TOKEN 時應改讀 GITLAB_TOKEN_FILE。"""
        with patch("builtins.open", mock_open(read_data="file-token\n")):
            token = resolve_token({"GITLAB_TOKEN_FILE": "/run/secrets/tk"})
        self.assertEqual(token, "file-token")

    def test_token_file_unreadable(self):
        """權杖檔讀取失敗時應回傳空字串而非例外。"""
        with patch("builtins.open", side_effect=OSError("denied")):
            self.assertEqual(
                resolve_token({"GITLAB_TOKEN_FILE": "/nope"}), ""
            )

    def test_no_token(self):
        """完全未設定時應回傳空字串。"""
        self.assertEqual(resolve_token({}), "")


class TestGitLabClient(unittest.TestCase):
    """測試 GitLab API 客戶端。"""

    def test_get_success(self):
        """2xx 應回傳解析後的 JSON。"""
        client = GitLabClient(base_url="http://gitlab:8080/", token="t")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [{"id": 1}]
        with patch.object(client.session, "get", return_value=response) as get:
            result = client.get("/runners", params={"per_page": 1})

        self.assertEqual(result, [{"id": 1}])
        args, kwargs = get.call_args
        self.assertEqual(args[0], "http://gitlab:8080/api/v4/runners")
        self.assertEqual(kwargs["params"], {"per_page": 1})

    def test_token_header_is_set(self):
        """權杖應以 PRIVATE-TOKEN 標頭傳送。"""
        client = GitLabClient(token="secret-token")
        self.assertEqual(
            client.session.headers.get("PRIVATE-TOKEN"), "secret-token"
        )

    def test_get_http_error(self):
        """非 2xx 應拋出 GitLabAPIError。"""
        client = GitLabClient(token="t")
        response = MagicMock()
        response.status_code = 403
        with patch.object(client.session, "get", return_value=response):
            with self.assertRaises(GitLabAPIError):
                client.get("/runners/all")

    def test_get_connection_error(self):
        """連線失敗應拋出 GitLabAPIError。"""
        client = GitLabClient(token="t")
        with patch.object(client.session, "get",
                          side_effect=requests.ConnectionError("down")):
            with self.assertRaises(GitLabAPIError):
                client.get("/version")

    def test_get_invalid_json(self):
        """回應無法解析時應拋出 GitLabAPIError。"""
        client = GitLabClient(token="t")
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("bad json")
        with patch.object(client.session, "get", return_value=response):
            with self.assertRaises(GitLabAPIError):
                client.get("/version")


class TestFetchRunnerStates(unittest.TestCase):
    """測試 runner 狀態蒐集。"""

    def test_online_runner_with_contact_time(self):
        """應算出最後聯繫至今的秒數。"""
        client = FakeGitLabClient(_build_responses())
        states = fetch_runner_states(client, GROUP_ID, NOW)

        self.assertEqual(len(states), 1)
        self.assertTrue(states[0]["online"])
        self.assertFalse(states[0]["paused"])
        self.assertAlmostEqual(states[0]["last_contact_seconds"], 300, delta=1)

    def test_runner_detail_failure_is_tolerated(self):
        """詳情查詢失敗時仍應回傳基本狀態。"""
        client = FakeGitLabClient(
            _build_responses(runner_online=False), failing_paths=["/runners/1"]
        )
        states = fetch_runner_states(client, GROUP_ID, NOW, MagicMock())

        self.assertFalse(states[0]["online"])
        self.assertIsNone(states[0]["last_contact_seconds"])

    def test_list_failure_raises(self):
        """列表查詢失敗應向上拋出。"""
        client = FakeGitLabClient(
            {}, failing_paths=["/groups/{}/runners".format(GROUP_ID)]
        )
        with self.assertRaises(GitLabAPIError):
            fetch_runner_states(client, GROUP_ID, NOW)


class TestFetchProjectState(unittest.TestCase):
    """測試單一專案 CI 狀態蒐集。"""

    def test_project_without_pipeline_is_skipped(self):
        """沒有任何 pipeline 的專案代表未設定 CI，應回傳 None。"""
        responses = _build_responses()
        responses["/projects/9/pipelines"] = []
        client = FakeGitLabClient(responses)

        state = fetch_project_state(
            client, {"id": 9, "path": "Tw_stock_client"}, NOW
        )
        self.assertIsNone(state)

    def test_failed_jobs_within_window_are_counted(self):
        """窗口內的失敗 job 應依 failure_reason 計數，過舊的排除。"""
        jobs = [
            {"failure_reason": STUCK_FAILURE_REASON,
             "finished_at": _iso(NOW - 3600)},
            {"failure_reason": STUCK_FAILURE_REASON,
             "finished_at": _iso(NOW - 7200)},
            {"failure_reason": "script_failure",
             "finished_at": _iso(NOW - 3600)},
            # 超過 24 小時窗口，不應計入
            {"failure_reason": STUCK_FAILURE_REASON,
             "finished_at": _iso(NOW - 200000)},
        ]
        client = FakeGitLabClient(_build_responses(failed_jobs=jobs))

        state = fetch_project_state(
            client, {"id": 9, "path": "Tw_stock_DB_Operating"}, NOW
        )

        self.assertEqual(state["failed_jobs"][STUCK_FAILURE_REASON], 2)
        self.assertEqual(state["failed_jobs"]["script_failure"], 1)

    def test_job_without_failure_reason(self):
        """缺少 failure_reason 時應歸類為 unknown。"""
        jobs = [{"finished_at": _iso(NOW - 60)}]
        client = FakeGitLabClient(_build_responses(failed_jobs=jobs))

        state = fetch_project_state(client, {"id": 9, "path": "p"}, NOW)
        self.assertEqual(state["failed_jobs"]["unknown"], 1)

    def test_tag_deployed_successfully(self):
        """tag 部署成功時未部署秒數應為 0。"""
        client = FakeGitLabClient(_build_responses())
        state = fetch_project_state(client, {"id": 9, "path": "p"}, NOW)

        self.assertEqual(state["tag"], "v2.15.0")
        self.assertEqual(state["tag_status"], "success")
        self.assertEqual(state["tag_undeployed_seconds"], 0)

    def test_tag_pipeline_failed(self):
        """tag pipeline 失敗時應算出未部署秒數。"""
        client = FakeGitLabClient(
            _build_responses(pipeline_status="failed", tag_status="failed")
        )
        state = fetch_project_state(client, {"id": 9, "path": "p"}, NOW)

        self.assertEqual(state["tag_status"], "failed")
        self.assertAlmostEqual(
            state["tag_undeployed_seconds"], 7200, delta=1
        )

    def test_tag_without_pipeline_is_missing(self):
        """tag 完全沒有 pipeline 時狀態應為 missing。"""
        responses = _build_responses(tag="v3.0.0")
        responses["/projects/9/pipelines"] = [
            {"id": 1, "ref": "main", "status": "success",
             "created_at": _iso(NOW - 100)}
        ]
        responses["/projects/9/pipelines?ref=v3.0.0"] = []
        client = FakeGitLabClient(responses)

        state = fetch_project_state(client, {"id": 9, "path": "p"}, NOW)

        self.assertEqual(state["tag_status"], "missing")
        self.assertGreater(state["tag_undeployed_seconds"], 0)

    def test_tag_pipeline_reuses_latest_when_ref_matches(self):
        """最新 pipeline 的 ref 就是最新 tag 時不應多打一次 API。"""
        client = FakeGitLabClient(_build_responses())
        fetch_project_state(client, {"id": 9, "path": "p"}, NOW)

        ref_queries = [
            call for call in client.calls
            if call[1] and call[1].get("ref")
        ]
        self.assertEqual(ref_queries, [])


class TestCollectGitLabCI(unittest.TestCase):
    """測試 GitLab CI 指標收集流程。"""

    def test_incident_signals_are_exposed(self):
        """事故情境（runner 離線 + job 卡住 + tag 未部署）應完整反映到指標。"""
        jobs = [
            {"failure_reason": STUCK_FAILURE_REASON,
             "finished_at": _iso(NOW - 600)},
            {"failure_reason": STUCK_FAILURE_REASON,
             "finished_at": _iso(NOW - 900)},
        ]
        client = FakeGitLabClient(
            _build_responses(pipeline_status="failed", tag="v2.13.0",
                             tag_status="failed", failed_jobs=jobs,
                             runner_online=False)
        )
        logger = MagicMock()

        result = collect_gitlab_ci(logger, client, GROUP_ID, now=NOW)

        self.assertTrue(result)
        self.assertEqual(registry.get_sample_value("tw_stock_gitlab_api_up"), 1)
        self.assertEqual(
            registry.get_sample_value("tw_stock_gitlab_runners_online_total"), 0
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_gitlab_runner_online",
                {"runner_id": "1", "description": "All-project-runner"},
            ),
            0,
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_gitlab_failed_jobs",
                {"project": "Tw_stock_DB_Operating",
                 "failure_reason": STUCK_FAILURE_REASON},
            ),
            2,
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_gitlab_pipeline_status",
                {"project": "Tw_stock_DB_Operating", "ref": "v2.13.0",
                 "status": "failed"},
            ),
            1,
        )
        self.assertGreater(
            registry.get_sample_value(
                "tw_stock_gitlab_tag_undeployed_seconds",
                {"project": "Tw_stock_DB_Operating", "tag": "v2.13.0"},
            ),
            1800,
        )

    def test_stale_series_are_cleared_between_cycles(self):
        """狀態變好之後，上一輪的舊 label 序列必須消失，避免永久誤報。"""
        logger = MagicMock()
        failed_client = FakeGitLabClient(
            _build_responses(pipeline_status="failed", tag="v2.13.0",
                             tag_status="failed")
        )
        collect_gitlab_ci(logger, failed_client, GROUP_ID, now=NOW)

        healthy_client = FakeGitLabClient(
            _build_responses(pipeline_status="success", tag="v2.15.0",
                             tag_status="success")
        )
        collect_gitlab_ci(logger, healthy_client, GROUP_ID, now=NOW)

        self.assertIsNone(
            registry.get_sample_value(
                "tw_stock_gitlab_pipeline_status",
                {"project": "Tw_stock_DB_Operating", "ref": "v2.13.0",
                 "status": "failed"},
            )
        )
        self.assertIsNone(
            registry.get_sample_value(
                "tw_stock_gitlab_tag_undeployed_seconds",
                {"project": "Tw_stock_DB_Operating", "tag": "v2.13.0"},
            )
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_gitlab_tag_undeployed_seconds",
                {"project": "Tw_stock_DB_Operating", "tag": "v2.15.0"},
            ),
            0,
        )

    def test_api_failure_keeps_previous_values(self):
        """GitLab 連不上時應只降 api_up，不清空既有指標。"""
        logger = MagicMock()
        collect_gitlab_ci(
            logger, FakeGitLabClient(_build_responses()), GROUP_ID, now=NOW
        )

        broken = FakeGitLabClient(
            {}, failing_paths=["/groups/{}/runners".format(GROUP_ID)]
        )
        result = collect_gitlab_ci(logger, broken, GROUP_ID, now=NOW)

        self.assertFalse(result)
        self.assertEqual(registry.get_sample_value("tw_stock_gitlab_api_up"), 0)
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_gitlab_pipeline_status",
                {"project": "Tw_stock_DB_Operating", "ref": "v2.15.0",
                 "status": "success"},
            ),
            1,
        )

    def test_single_project_failure_does_not_abort(self):
        """單一專案查詢失敗不應中斷其他專案與 runner 指標。"""
        responses = _build_responses()
        responses["/groups/{}/projects".format(GROUP_ID)] = [
            {"id": 9, "path": "Tw_stock_DB_Operating"},
            {"id": 99, "path": "Broken_project"},
        ]
        client = FakeGitLabClient(
            responses, failing_paths=["/projects/99/pipelines"]
        )
        logger = MagicMock()

        result = collect_gitlab_ci(logger, client, GROUP_ID, now=NOW)

        self.assertTrue(result)
        logger.error.assert_called()
        self.assertEqual(
            registry.get_sample_value("tw_stock_gitlab_runners_total"), 1
        )

    def test_last_collect_timestamp_is_updated(self):
        """成功收集後應更新最後收集時間。"""
        collect_gitlab_ci(
            MagicMock(), FakeGitLabClient(_build_responses()), GROUP_ID, now=NOW
        )
        self.assertEqual(
            registry.get_sample_value(
                "tw_stock_gitlab_last_collect_timestamp_seconds"
            ),
            NOW,
        )


if __name__ == "__main__":
    unittest.main()
