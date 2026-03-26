"""macOS 主機指標 Exporter。

在 macOS 主機上直接執行，透過 psutil 收集系統指標，
以 Prometheus 格式暴露給 Prometheus 抓取。

指標命名遵循 Node Exporter 慣例，方便儀表板相容。
"""

import logging
import os
import subprocess
import time

import psutil
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily

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

# --- 程序排行榜共用常數 ---
TOP_PROCESSES_COUNT = 15
SUBPROCESS_TIMEOUT = 30


def _get_top_memory_processes(top_n=TOP_PROCESSES_COUNT):
    """取得記憶體使用量最高的前 N 個程序。

    遍歷系統所有程序，依照 RSS（Resident Set Size）排序，
    回傳記憶體使用量最高的前 N 個程序資訊。

    Args:
        top_n: 要回傳的程序數量，預設為 TOP_PROCESSES_COUNT。

    Returns:
        包含 (name, pid, rss_bytes) 元組的列表，
        依 rss_bytes 由大到小排序。
    """
    processes = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            mem_info = info.get("memory_info")
            if mem_info is None:
                continue
            processes.append((
                info["name"] or "unknown",
                info["pid"],
                mem_info.rss,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    # 依 RSS 由大到小排序，取前 N 名
    processes.sort(key=lambda x: x[2], reverse=True)
    return processes[:top_n]


class TopProcessesCollector:
    """自訂 Prometheus Collector：暴露記憶體使用量前 N 名程序。

    使用自訂 Collector（而非固定 Gauge）的原因：
    - 每次抓取時動態生成指標，避免舊的 label 組合殘留
    - 程序可能隨時啟動或終止，label 集合每次都不同
    """

    def describe(self):
        """回傳空列表，表示此 Collector 不預先宣告指標。"""
        return []

    def collect(self):
        """收集記憶體使用量前 N 名程序的指標。

        Yields:
            GaugeMetricFamily: 包含每個程序的 RSS 記憶體使用量（bytes），
            以 process_name、pid 和 rank 作為 label。
        """
        gauge = GaugeMetricFamily(
            "node_top_memory_process_rss_bytes",
            "記憶體使用量前 N 名程序的 RSS（Resident Set Size），單位 bytes",
            labels=["process_name", "pid", "rank"],
        )
        top_processes = _get_top_memory_processes()
        for rank, (name, pid, rss) in enumerate(top_processes, start=1):
            gauge.add_metric([name, str(pid), str(rank)], rss)
        yield gauge


# --- 程序網路流量前 N 名 ---

logger = logging.getLogger(__name__)


def _get_top_network_processes(top_n=TOP_PROCESSES_COUNT):
    """透過 nettop 取得網路流量最高的前 N 個程序。

    執行 macOS 內建的 nettop 命令，解析 CSV 輸出取得每個程序的
    bytes_in 與 bytes_out，依總流量（bytes_in + bytes_out）排序。

    Args:
        top_n: 要回傳的程序數量，預設為 TOP_PROCESSES_COUNT。

    Returns:
        包含 (name, pid, bytes_in, bytes_out) 元組的列表，
        依總流量由大到小排序。若命令執行失敗則回傳空列表。
    """
    try:
        result = subprocess.run(
            [
                "nettop", "-P", "-L", "1",
                "-J", "bytes_in,bytes_out",
                "-x",
                "-t", "wifi",
                "-t", "wired",
            ],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("nettop 命令執行逾時（%d 秒）", SUBPROCESS_TIMEOUT)
        return []
    except FileNotFoundError:
        logger.warning("找不到 nettop 命令，可能不在 macOS 環境")
        return []
    except OSError as e:
        logger.warning("執行 nettop 時發生作業系統錯誤: %s", e)
        return []

    return _parse_nettop_output(result.stdout, top_n)


def _parse_nettop_output(output, top_n=TOP_PROCESSES_COUNT):
    """解析 nettop 的 CSV 輸出。

    nettop 輸出格式範例：
        ,bytes_in,bytes_out,
        Chrome.12345,1024,2048,
        Safari.67890,512,256,

    Args:
        output: nettop 命令的標準輸出字串。
        top_n: 要回傳的程序數量。

    Returns:
        包含 (name, pid, bytes_in, bytes_out) 元組的列表，
        依總流量由大到小排序。
    """
    processes = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(","):
            # 跳過空行和標題行（以逗號開頭）
            continue
        parts = line.rstrip(",").split(",")
        if len(parts) < 3:
            continue
        identity = parts[0]
        try:
            bytes_in = int(parts[1])
            bytes_out = int(parts[2])
        except (ValueError, IndexError):
            continue
        # 解析 process_name.pid 格式
        dot_idx = identity.rfind(".")
        if dot_idx == -1:
            continue
        name = identity[:dot_idx]
        pid_str = identity[dot_idx + 1:]
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if bytes_in == 0 and bytes_out == 0:
            continue
        processes.append((name, pid, bytes_in, bytes_out))

    processes.sort(key=lambda x: x[2] + x[3], reverse=True)
    return processes[:top_n]


class TopNetworkProcessesCollector:
    """自訂 Prometheus Collector：暴露網路流量前 N 名程序。

    透過 nettop 命令取得 per-process 網路流量，
    每次抓取時動態生成指標，避免舊的 label 組合殘留。
    """

    def describe(self):
        """回傳空列表，表示此 Collector 不預先宣告指標。"""
        return []

    def collect(self):
        """收集網路流量前 N 名程序的指標。

        Yields:
            GaugeMetricFamily: 包含每個程序的網路流量（bytes），
            以 process_name、pid、rank 和 direction（in/out）作為 label。
        """
        gauge = GaugeMetricFamily(
            "node_top_network_process_bytes",
            "網路流量前 N 名程序的傳輸量，單位 bytes",
            labels=["process_name", "pid", "rank", "direction"],
        )
        top_processes = _get_top_network_processes()
        for rank, (name, pid, bytes_in, bytes_out) in enumerate(
            top_processes, start=1
        ):
            gauge.add_metric(
                [name, str(pid), str(rank), "in"], bytes_in
            )
            gauge.add_metric(
                [name, str(pid), str(rank), "out"], bytes_out
            )
        yield gauge


# --- 程序耗電量前 N 名 ---


def _get_top_power_processes(top_n=TOP_PROCESSES_COUNT):
    """透過 top 命令取得耗電量最高的前 N 個程序。

    執行 macOS 內建的 top 命令（-l 2 取第二次採樣），
    解析 power 欄位取得每個程序的 energy impact 值。

    Args:
        top_n: 要回傳的程序數量，預設為 TOP_PROCESSES_COUNT。

    Returns:
        包含 (name, pid, power) 元組的列表，
        依 power 由大到小排序。若命令執行失敗則回傳空列表。
    """
    try:
        result = subprocess.run(
            [
                "top", "-l", "2",
                "-n", str(top_n + 5),
                "-stats", "pid,command,power",
                "-o", "power",
            ],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("top 命令執行逾時（%d 秒）", SUBPROCESS_TIMEOUT)
        return []
    except FileNotFoundError:
        logger.warning("找不到 top 命令")
        return []
    except OSError as e:
        logger.warning("執行 top 時發生作業系統錯誤: %s", e)
        return []

    return _parse_top_power_output(result.stdout, top_n)


def _parse_top_power_output(output, top_n=TOP_PROCESSES_COUNT):
    """解析 top 命令的 power 輸出。

    top -l 2 會輸出兩次採樣，第二次採樣才有準確的 power 數據。
    格式範例（第二次採樣的程序區塊）：
        PID    COMMAND          POWER
        592    WindowServer     43.6
        28961  Google Chrome He 42.4

    Args:
        output: top 命令的標準輸出字串。
        top_n: 要回傳的程序數量。

    Returns:
        包含 (name, pid, power) 元組的列表，
        依 power 由大到小排序。
    """
    lines = output.splitlines()
    # 找到第二個 "PID" 標題行（第二次採樣的程序列表）
    header_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("PID") and "POWER" in line.upper()
    ]
    if len(header_indices) < 2:
        logger.warning("無法在 top 輸出中找到第二次採樣的 POWER 標題")
        return []

    second_header_idx = header_indices[1]
    processes = []
    for line in lines[second_header_idx + 1:]:
        line = line.strip()
        if not line:
            continue
        # 遇到非程序資料行（如 "Processes:"）時停止
        if line.startswith("Processes:") or line.startswith("Load Avg:"):
            break
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        # power 是最後一個欄位，command 可能包含空格
        try:
            power = float(parts[-1])
        except ValueError:
            continue
        # command 是中間所有欄位（去頭去尾）
        name = " ".join(parts[1:-1])
        if power <= 0:
            continue
        processes.append((name, pid, power))

    processes.sort(key=lambda x: x[2], reverse=True)
    return processes[:top_n]


class TopPowerProcessesCollector:
    """自訂 Prometheus Collector：暴露耗電量前 N 名程序。

    透過 top 命令取得 per-process energy impact，
    每次抓取時動態生成指標，避免舊的 label 組合殘留。
    """

    def describe(self):
        """回傳空列表，表示此 Collector 不預先宣告指標。"""
        return []

    def collect(self):
        """收集耗電量前 N 名程序的指標。

        Yields:
            GaugeMetricFamily: 包含每個程序的 energy impact 值，
            以 process_name、pid 和 rank 作為 label。
        """
        gauge = GaugeMetricFamily(
            "node_top_power_process_energy",
            "耗電量前 N 名程序的 energy impact 值",
            labels=["process_name", "pid", "rank"],
        )
        top_processes = _get_top_power_processes()
        for rank, (name, pid, power) in enumerate(top_processes, start=1):
            gauge.add_metric([name, str(pid), str(rank)], power)
        yield gauge


# 註冊自訂 Collectors
registry.register(TopProcessesCollector())
registry.register(TopNetworkProcessesCollector())
registry.register(TopPowerProcessesCollector())


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


def _is_meaningful_partition(part):
    """判斷磁碟分區是否為有意義的監控對象。

    macOS 使用 APFS 容器，同一物理磁碟會被分為多個卷宗（volumes），
    這些卷宗共享相同的儲存池，導致 psutil 報告重複的 total/free 值。
    只保留真正有意義的掛載點，避免虛假的重複資料干擾監控。

    Args:
        part: psutil.disk_partitions() 回傳的分區物件。

    Returns:
        True 表示應該監控此分區，False 表示應跳過。
    """
    # 排除不需要的檔案系統類型
    excluded_fstypes = ("devfs", "autofs", "nullfs")
    if part.fstype in excluded_fstypes:
        return False

    # macOS APFS 系統卷宗過濾：只保留根目錄和資料卷宗
    # 其他系統卷宗（VM、Preboot、Update、xarts、iSCPreboot、Hardware）
    # 都共享同一個 APFS 容器的可用空間，監控它們會產生重複且無意義的數據
    excluded_mountpoint_prefixes = (
        "/System/Volumes/VM",
        "/System/Volumes/Preboot",
        "/System/Volumes/Update",
        "/System/Volumes/xarts",
        "/System/Volumes/iSCPreboot",
        "/System/Volumes/Hardware",
        "/Library/Developer/CoreSimulator/Volumes",
    )
    if part.mountpoint.startswith(excluded_mountpoint_prefixes):
        return False

    return True


def _collect_filesystem():
    """收集檔案系統指標。

    在 macOS 上，APFS 容器內的多個卷宗共享儲存池，
    因此只監控有意義的掛載點（/、/System/Volumes/Data、外接磁碟等）。
    """
    for part in psutil.disk_partitions(all=False):
        if not _is_meaningful_partition(part):
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
