"""macOS Exporter 單元測試。

測試磁碟分區過濾邏輯與程序記憶體使用量收集功能，
確保只監控有意義的掛載點，以及正確收集前 N 名程序資訊。
"""

import unittest
from collections import namedtuple
from unittest.mock import MagicMock, patch

from src.macos_exporter import (
    TopProcessesCollector,
    _get_top_memory_processes,
    _is_meaningful_partition,
)

# 模擬 psutil 的 sdiskpart 結構
MockPartition = namedtuple("MockPartition", ["device", "mountpoint", "fstype", "opts"])


class TestIsMeaningfulPartition(unittest.TestCase):
    """測試 _is_meaningful_partition 過濾函式。"""

    def test_root_partition_is_meaningful(self):
        """根目錄 / 應被視為有意義的分區。"""
        part = MockPartition(
            device="/dev/disk3s3s1",
            mountpoint="/",
            fstype="apfs",
            opts="ro,local,rootfs,dovolfs,journaled,multilabel",
        )
        self.assertTrue(_is_meaningful_partition(part))

    def test_data_volume_is_meaningful(self):
        """使用者資料卷宗 /System/Volumes/Data 應被視為有意義的分區。"""
        part = MockPartition(
            device="/dev/disk3s1",
            mountpoint="/System/Volumes/Data",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertTrue(_is_meaningful_partition(part))

    def test_vm_volume_is_excluded(self):
        """虛擬記憶體卷宗 /System/Volumes/VM 應被排除。"""
        part = MockPartition(
            device="/dev/disk3s6",
            mountpoint="/System/Volumes/VM",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_preboot_volume_is_excluded(self):
        """開機前置卷宗 /System/Volumes/Preboot 應被排除。"""
        part = MockPartition(
            device="/dev/disk3s4",
            mountpoint="/System/Volumes/Preboot",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_update_volume_is_excluded(self):
        """系統更新卷宗 /System/Volumes/Update 應被排除。"""
        part = MockPartition(
            device="/dev/disk3s2",
            mountpoint="/System/Volumes/Update",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_xarts_volume_is_excluded(self):
        """安全卷宗 /System/Volumes/xarts 應被排除。"""
        part = MockPartition(
            device="/dev/disk1s2",
            mountpoint="/System/Volumes/xarts",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_iscpreboot_volume_is_excluded(self):
        """iSC 開機前置卷宗 /System/Volumes/iSCPreboot 應被排除。"""
        part = MockPartition(
            device="/dev/disk1s1",
            mountpoint="/System/Volumes/iSCPreboot",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_hardware_volume_is_excluded(self):
        """硬體卷宗 /System/Volumes/Hardware 應被排除。"""
        part = MockPartition(
            device="/dev/disk1s3",
            mountpoint="/System/Volumes/Hardware",
            fstype="apfs",
            opts="local,dovolfs,journaled,multilabel",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_devfs_is_excluded(self):
        """devfs 檔案系統應被排除。"""
        part = MockPartition(
            device="devfs",
            mountpoint="/dev",
            fstype="devfs",
            opts="local,nobrowse",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_autofs_is_excluded(self):
        """autofs 檔案系統應被排除。"""
        part = MockPartition(
            device="map auto_home",
            mountpoint="/System/Volumes/Data/home",
            fstype="autofs",
            opts="nobrowse",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_nullfs_is_excluded(self):
        """nullfs 檔案系統應被排除。"""
        part = MockPartition(
            device="nullfs",
            mountpoint="/some/mount",
            fstype="nullfs",
            opts="",
        )
        self.assertFalse(_is_meaningful_partition(part))

    def test_external_hfs_disk_is_meaningful(self):
        """外接 HFS 磁碟應被視為有意義的分區。"""
        part = MockPartition(
            device="/dev/disk4s1",
            mountpoint="/Volumes/ExternalDisk",
            fstype="hfs",
            opts="local,nodev,nosuid,journaled",
        )
        self.assertTrue(_is_meaningful_partition(part))

    def test_external_apfs_disk_is_meaningful(self):
        """外接 APFS 磁碟應被視為有意義的分區。"""
        part = MockPartition(
            device="/dev/disk5s1",
            mountpoint="/Volumes/MyUSB",
            fstype="apfs",
            opts="local,nodev,nosuid,journaled",
        )
        self.assertTrue(_is_meaningful_partition(part))

    def test_linux_ext4_partition_is_meaningful(self):
        """Linux ext4 分區也應被視為有意義的分區（跨平台相容）。"""
        part = MockPartition(
            device="/dev/sda1",
            mountpoint="/",
            fstype="ext4",
            opts="rw,relatime",
        )
        self.assertTrue(_is_meaningful_partition(part))


class TestCollectFilesystem(unittest.TestCase):
    """測試 _collect_filesystem 函式的整合行為。"""

    @patch("src.macos_exporter.psutil")
    def test_collect_filesystem_filters_system_volumes(self, mock_psutil):
        """確認 _collect_filesystem 會過濾掉 macOS 系統卷宗。"""
        # 模擬 macOS 典型的磁碟分區列表
        mock_psutil.disk_partitions.return_value = [
            MockPartition("/dev/disk3s3s1", "/", "apfs", ""),
            MockPartition("/dev/disk3s6", "/System/Volumes/VM", "apfs", ""),
            MockPartition("/dev/disk3s4", "/System/Volumes/Preboot", "apfs", ""),
            MockPartition("/dev/disk3s2", "/System/Volumes/Update", "apfs", ""),
            MockPartition("/dev/disk1s2", "/System/Volumes/xarts", "apfs", ""),
            MockPartition("/dev/disk1s1", "/System/Volumes/iSCPreboot", "apfs", ""),
            MockPartition("/dev/disk1s3", "/System/Volumes/Hardware", "apfs", ""),
            MockPartition("/dev/disk3s1", "/System/Volumes/Data", "apfs", ""),
        ]

        # 模擬 disk_usage 回傳值
        mock_usage = MagicMock()
        mock_usage.total = 994662584320
        mock_usage.free = 557692219392
        mock_psutil.disk_usage.return_value = mock_usage

        from src.macos_exporter import _collect_filesystem

        _collect_filesystem()

        # 應該只對 / 和 /System/Volumes/Data 呼叫 disk_usage
        call_args = [
            call[0][0] for call in mock_psutil.disk_usage.call_args_list
        ]
        self.assertIn("/", call_args)
        self.assertIn("/System/Volumes/Data", call_args)
        self.assertNotIn("/System/Volumes/VM", call_args)
        self.assertNotIn("/System/Volumes/Preboot", call_args)
        self.assertNotIn("/System/Volumes/Update", call_args)
        self.assertNotIn("/System/Volumes/xarts", call_args)
        self.assertNotIn("/System/Volumes/iSCPreboot", call_args)
        self.assertNotIn("/System/Volumes/Hardware", call_args)


class TestGetTopMemoryProcesses(unittest.TestCase):
    """測試 _get_top_memory_processes 函式。"""

    @patch("src.macos_exporter.psutil")
    def test_returns_top_n_by_rss(self, mock_psutil):
        """應回傳依 RSS 排序的前 N 名程序。"""
        # 建立 5 個模擬程序
        mock_procs = []
        rss_values = [100, 500, 200, 800, 300]
        for i, rss in enumerate(rss_values):
            proc = MagicMock()
            mem_info = MagicMock()
            mem_info.rss = rss
            proc.info = {
                "pid": 1000 + i,
                "name": f"proc_{i}",
                "memory_info": mem_info,
            }
            mock_procs.append(proc)

        mock_psutil.process_iter.return_value = mock_procs
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception
        mock_psutil.ZombieProcess = Exception

        result = _get_top_memory_processes(top_n=3)

        self.assertEqual(len(result), 3)
        # 驗證依 RSS 由大到小排序
        self.assertEqual(result[0], ("proc_3", 1003, 800))
        self.assertEqual(result[1], ("proc_1", 1001, 500))
        self.assertEqual(result[2], ("proc_4", 1004, 300))

    @patch("src.macos_exporter.psutil")
    def test_handles_fewer_processes_than_top_n(self, mock_psutil):
        """當系統程序數少於 top_n 時，應回傳所有程序。"""
        proc = MagicMock()
        mem_info = MagicMock()
        mem_info.rss = 1024
        proc.info = {"pid": 1, "name": "single", "memory_info": mem_info}
        mock_psutil.process_iter.return_value = [proc]
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception
        mock_psutil.ZombieProcess = Exception

        result = _get_top_memory_processes(top_n=15)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("single", 1, 1024))

    @patch("src.macos_exporter.psutil")
    def test_skips_process_without_memory_info(self, mock_psutil):
        """memory_info 為 None 的程序應被跳過。"""
        proc_with_mem = MagicMock()
        mem_info = MagicMock()
        mem_info.rss = 2048
        proc_with_mem.info = {
            "pid": 10,
            "name": "has_mem",
            "memory_info": mem_info,
        }
        proc_without_mem = MagicMock()
        proc_without_mem.info = {
            "pid": 20,
            "name": "no_mem",
            "memory_info": None,
        }
        mock_psutil.process_iter.return_value = [
            proc_with_mem,
            proc_without_mem,
        ]
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception
        mock_psutil.ZombieProcess = Exception

        result = _get_top_memory_processes(top_n=15)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("has_mem", 10, 2048))

    @patch("src.macos_exporter.psutil")
    def test_handles_access_denied(self, mock_psutil):
        """遇到 AccessDenied 的程序應被跳過，不影響其他程序。"""
        good_proc = MagicMock()
        mem_info = MagicMock()
        mem_info.rss = 4096
        good_proc.info = {"pid": 1, "name": "good", "memory_info": mem_info}

        # 模擬 AccessDenied 例外
        class MockAccessDenied(Exception):
            pass

        bad_proc = MagicMock()
        bad_proc.info = property(lambda self: None)
        type(bad_proc).info = property(
            lambda self: (_ for _ in ()).throw(MockAccessDenied())
        )

        mock_psutil.process_iter.return_value = [good_proc]
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception
        mock_psutil.ZombieProcess = Exception

        result = _get_top_memory_processes(top_n=15)

        self.assertEqual(len(result), 1)

    @patch("src.macos_exporter.psutil")
    def test_handles_none_process_name(self, mock_psutil):
        """程序名稱為 None 時應替換為 'unknown'。"""
        proc = MagicMock()
        mem_info = MagicMock()
        mem_info.rss = 512
        proc.info = {"pid": 99, "name": None, "memory_info": mem_info}
        mock_psutil.process_iter.return_value = [proc]
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception
        mock_psutil.ZombieProcess = Exception

        result = _get_top_memory_processes(top_n=15)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "unknown")


class TestTopProcessesCollector(unittest.TestCase):
    """測試 TopProcessesCollector 自訂 Prometheus Collector。"""

    @patch("src.macos_exporter._get_top_memory_processes")
    def test_collect_yields_gauge_metric_family(self, mock_get_top):
        """collect() 應產生包含程序指標的 GaugeMetricFamily。"""
        mock_get_top.return_value = [
            ("chrome", 123, 1073741824),
            ("python", 456, 536870912),
        ]

        collector = TopProcessesCollector()
        metrics = list(collector.collect())

        self.assertEqual(len(metrics), 1)
        metric = metrics[0]
        self.assertEqual(metric.name, "node_top_memory_process_rss_bytes")
        self.assertEqual(len(metric.samples), 2)

    @patch("src.macos_exporter._get_top_memory_processes")
    def test_collect_includes_correct_labels(self, mock_get_top):
        """指標應包含 process_name、pid 和 rank labels。"""
        mock_get_top.return_value = [
            ("firefox", 789, 2147483648),
        ]

        collector = TopProcessesCollector()
        metrics = list(collector.collect())
        sample = metrics[0].samples[0]

        self.assertEqual(sample.labels["process_name"], "firefox")
        self.assertEqual(sample.labels["pid"], "789")
        self.assertEqual(sample.labels["rank"], "1")
        self.assertEqual(sample.value, 2147483648)

    @patch("src.macos_exporter._get_top_memory_processes")
    def test_collect_empty_when_no_processes(self, mock_get_top):
        """沒有程序時應回傳空的 GaugeMetricFamily。"""
        mock_get_top.return_value = []

        collector = TopProcessesCollector()
        metrics = list(collector.collect())

        self.assertEqual(len(metrics), 1)
        self.assertEqual(len(metrics[0].samples), 0)

    def test_describe_returns_empty(self):
        """describe() 應回傳空列表（不預先宣告指標）。"""
        collector = TopProcessesCollector()
        self.assertEqual(list(collector.describe()), [])


if __name__ == "__main__":
    unittest.main()
