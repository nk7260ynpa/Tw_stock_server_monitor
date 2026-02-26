"""macOS Exporter 單元測試。

測試磁碟分區過濾邏輯，確保只監控有意義的掛載點。
"""

import unittest
from collections import namedtuple
from unittest.mock import MagicMock, patch

from src.macos_exporter import _is_meaningful_partition

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


if __name__ == "__main__":
    unittest.main()
