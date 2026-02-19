"""macOS 主機指標 Exporter。

在 macOS 主機上直接執行，透過 psutil 收集系統指標，
以 Prometheus 格式暴露給 Prometheus 抓取。

指標命名遵循 Node Exporter 慣例，方便儀表板相容。
"""

import logging
import os
import time

import psutil
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    start_http_server,
)

from src.logger import setup_logger

# 預設監聽端口
DEFAULT_PORT = 9101

# 建立獨立的 registry，避免預設 collector 干擾
registry = CollectorRegistry()

# --- 系統資訊 ---
boot_time_gauge = Gauge(
    "node_boot_time_seconds",
    "macOS 系統開機時間（Unix timestamp）",
    registry=registry,
)

# --- CPU ---
cpu_seconds_total = Counter(
    "node_cpu_seconds_total",
    "CPU 各模式累計使用秒數",
    ["cpu", "mode"],
    registry=registry,
)

load1_gauge = Gauge("node_load1", "1 分鐘平均負載", registry=registry)
load5_gauge = Gauge("node_load5", "5 分鐘平均負載", registry=registry)
load15_gauge = Gauge("node_load15", "15 分鐘平均負載", registry=registry)

# --- 記憶體 ---
mem_total = Gauge(
    "node_memory_MemTotal_bytes", "總記憶體（bytes）", registry=registry
)
mem_available = Gauge(
    "node_memory_MemAvailable_bytes", "可用記憶體（bytes）", registry=registry
)
mem_free = Gauge(
    "node_memory_MemFree_bytes", "空閒記憶體（bytes）", registry=registry
)
mem_buffers = Gauge(
    "node_memory_Buffers_bytes", "Buffers 記憶體（bytes）", registry=registry
)
mem_cached = Gauge(
    "node_memory_Cached_bytes", "Cached 記憶體（bytes）", registry=registry
)
swap_total = Gauge(
    "node_memory_SwapTotal_bytes", "Swap 總量（bytes）", registry=registry
)
swap_free = Gauge(
    "node_memory_SwapFree_bytes", "Swap 可用（bytes）", registry=registry
)

# --- 磁碟 ---
fs_size = Gauge(
    "node_filesystem_size_bytes",
    "檔案系統總大小（bytes）",
    ["device", "fstype", "mountpoint"],
    registry=registry,
)
fs_avail = Gauge(
    "node_filesystem_avail_bytes",
    "檔案系統可用空間（bytes）",
    ["device", "fstype", "mountpoint"],
    registry=registry,
)
disk_read_bytes = Counter(
    "node_disk_read_bytes_total",
    "磁碟讀取位元組數",
    ["device"],
    registry=registry,
)
disk_written_bytes = Counter(
    "node_disk_written_bytes_total",
    "磁碟寫入位元組數",
    ["device"],
    registry=registry,
)

# --- 網路 ---
net_receive_bytes = Counter(
    "node_network_receive_bytes_total",
    "網路接收位元組數",
    ["device"],
    registry=registry,
)
net_transmit_bytes = Counter(
    "node_network_transmit_bytes_total",
    "網路傳送位元組數",
    ["device"],
    registry=registry,
)
net_receive_packets = Counter(
    "node_network_receive_packets_total",
    "網路接收封包數",
    ["device"],
    registry=registry,
)
net_transmit_packets = Counter(
    "node_network_transmit_packets_total",
    "網路傳送封包數",
    ["device"],
    registry=registry,
)

# 記錄上一次的計數器值，用於計算增量
_prev_cpu_times = {}
_prev_disk_io = {}
_prev_net_io = {}


def _collect_boot_time():
    """收集系統開機時間。"""
    boot_time_gauge.set(psutil.boot_time())


def _collect_cpu():
    """收集 CPU 指標。"""
    global _prev_cpu_times

    # 各 CPU 核心的使用時間
    per_cpu = psutil.cpu_times(percpu=True)
    for i, cpu in enumerate(per_cpu):
        cpu_label = str(i)
        current = {
            "user": cpu.user,
            "system": cpu.system,
            "idle": cpu.idle,
            "nice": cpu.nice,
        }
        prev = _prev_cpu_times.get(cpu_label, {})
        for mode, value in current.items():
            delta = value - prev.get(mode, 0)
            if delta > 0:
                cpu_seconds_total.labels(cpu=cpu_label, mode=mode).inc(delta)
        _prev_cpu_times[cpu_label] = current

    # 負載
    load1, load5, load15 = os.getloadavg()
    load1_gauge.set(load1)
    load5_gauge.set(load5)
    load15_gauge.set(load15)


def _collect_memory():
    """收集記憶體指標。"""
    vm = psutil.virtual_memory()
    mem_total.set(vm.total)
    mem_available.set(vm.available)
    mem_free.set(vm.free)
    # macOS 沒有 buffers，以 inactive 代替
    mem_buffers.set(getattr(vm, "buffers", 0))
    mem_cached.set(getattr(vm, "cached", vm.inactive))

    swap = psutil.swap_memory()
    swap_total.set(swap.total)
    swap_free.set(swap.free)


def _collect_filesystem():
    """收集檔案系統指標。"""
    for part in psutil.disk_partitions(all=False):
        # 排除不需要的檔案系統類型
        if part.fstype in ("devfs", "autofs", "nullfs"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        fs_size.labels(
            device=part.device,
            fstype=part.fstype,
            mountpoint=part.mountpoint,
        ).set(usage.total)
        fs_avail.labels(
            device=part.device,
            fstype=part.fstype,
            mountpoint=part.mountpoint,
        ).set(usage.free)


def _collect_disk_io():
    """收集磁碟 I/O 指標。"""
    global _prev_disk_io

    try:
        io_counters = psutil.disk_io_counters(perdisk=True)
    except RuntimeError:
        return

    for device, counters in io_counters.items():
        prev = _prev_disk_io.get(device, {})
        read_delta = counters.read_bytes - prev.get("read_bytes", 0)
        write_delta = counters.write_bytes - prev.get("write_bytes", 0)
        if read_delta > 0:
            disk_read_bytes.labels(device=device).inc(read_delta)
        if write_delta > 0:
            disk_written_bytes.labels(device=device).inc(write_delta)
        _prev_disk_io[device] = {
            "read_bytes": counters.read_bytes,
            "write_bytes": counters.write_bytes,
        }


def _collect_network():
    """收集網路指標。"""
    global _prev_net_io

    net_io = psutil.net_io_counters(pernic=True)
    for device, counters in net_io.items():
        if device == "lo0":
            continue
        prev = _prev_net_io.get(device, {})

        recv_delta = counters.bytes_recv - prev.get("bytes_recv", 0)
        sent_delta = counters.bytes_sent - prev.get("bytes_sent", 0)
        recv_pkt_delta = counters.packets_recv - prev.get("packets_recv", 0)
        sent_pkt_delta = counters.packets_sent - prev.get("packets_sent", 0)

        if recv_delta > 0:
            net_receive_bytes.labels(device=device).inc(recv_delta)
        if sent_delta > 0:
            net_transmit_bytes.labels(device=device).inc(sent_delta)
        if recv_pkt_delta > 0:
            net_receive_packets.labels(device=device).inc(recv_pkt_delta)
        if sent_pkt_delta > 0:
            net_transmit_packets.labels(device=device).inc(sent_pkt_delta)

        _prev_net_io[device] = {
            "bytes_recv": counters.bytes_recv,
            "bytes_sent": counters.bytes_sent,
            "packets_recv": counters.packets_recv,
            "packets_sent": counters.packets_sent,
        }


def collect_all():
    """收集所有系統指標。"""
    _collect_boot_time()
    _collect_cpu()
    _collect_memory()
    _collect_filesystem()
    _collect_disk_io()
    _collect_network()


def main():
    """啟動 macOS metrics exporter。"""
    logger = setup_logger("macos_exporter")

    port = int(os.environ.get("MACOS_EXPORTER_PORT", DEFAULT_PORT))

    # 啟動 HTTP server
    start_http_server(port, registry=registry)
    logger.info("macOS Exporter 已啟動，監聽端口 %d", port)

    # 初始化計數器基準值
    collect_all()

    # 定期收集指標
    interval = int(os.environ.get("MACOS_EXPORTER_INTERVAL", 10))
    while True:
        try:
            collect_all()
        except Exception:
            logger.exception("收集指標時發生錯誤")
        time.sleep(interval)


if __name__ == "__main__":
    main()
