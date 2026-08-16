"""時間字串解析工具單元測試。"""

import unittest
from datetime import datetime, timezone

from src.timeutils import parse_iso_timestamp


class TestParseIsoTimestamp(unittest.TestCase):
    """測試 ISO 8601 時間字串解析。"""

    def test_docker_nanosecond_precision(self):
        """Docker 的奈秒精度應截斷至微秒後正確解析。"""
        result = parse_iso_timestamp("2026-08-16T00:28:44.381378677Z")
        expected = datetime(
            2026, 8, 16, 0, 28, 44, 381378, tzinfo=timezone.utc
        ).timestamp()
        self.assertAlmostEqual(result, expected, places=6)

    def test_gitlab_millisecond_precision(self):
        """GitLab 的毫秒精度應正確解析。"""
        result = parse_iso_timestamp("2026-08-16T02:05:34.533Z")
        expected = datetime(
            2026, 8, 16, 2, 5, 34, 533000, tzinfo=timezone.utc
        ).timestamp()
        self.assertAlmostEqual(result, expected, places=6)

    def test_explicit_offset(self):
        """帶時區位移的字串應正確解析。"""
        utc_value = parse_iso_timestamp("2026-08-15T10:35:05.000+00:00")
        plus8_value = parse_iso_timestamp("2026-08-15T18:35:05.000+08:00")
        self.assertEqual(utc_value, plus8_value)

    def test_without_fraction(self):
        """沒有小數秒也應可解析。"""
        result = parse_iso_timestamp("2026-08-16T02:05:34Z")
        expected = datetime(
            2026, 8, 16, 2, 5, 34, tzinfo=timezone.utc
        ).timestamp()
        self.assertEqual(result, expected)

    def test_naive_time_is_utc(self):
        """未帶時區資訊時應視為 UTC。"""
        self.assertEqual(
            parse_iso_timestamp("2026-08-16T02:05:34"),
            parse_iso_timestamp("2026-08-16T02:05:34Z"),
        )

    def test_none_and_empty(self):
        """None 與空字串應回傳 None。"""
        self.assertIsNone(parse_iso_timestamp(None))
        self.assertIsNone(parse_iso_timestamp(""))

    def test_invalid_string(self):
        """無法解析的字串應回傳 None 而非拋出例外。"""
        self.assertIsNone(parse_iso_timestamp("not-a-time"))
        self.assertIsNone(parse_iso_timestamp("2026-13-45T99:99:99Z"))


if __name__ == "__main__":
    unittest.main()
