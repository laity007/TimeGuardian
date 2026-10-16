import socket
import struct
import time
import subprocess
import re
from typing import Optional, Tuple, List, Callable
import threading
import statistics

# NTP constants
NTP_PORT = 123
NTP_DELTA = 2208988800  # seconds (NTP epoch 1900)

class NtpResult:
    def __init__(self, server: str, rtt_ms: float, offset_ms: float, success: bool, error: Optional[str] = None):
        self.server = server
        self.rtt_ms = rtt_ms
        self.offset_ms = offset_ms
        self.success = success
        self.error = error  # when not None, includes reason or fallback method (e.g., "已改用 ICMP 测试延迟")

def _system_to_ntp_time(timestamp: float) -> float:
    return timestamp + NTP_DELTA

def _ntp_to_system_time(timestamp: float) -> float:
    return timestamp - NTP_DELTA

def _timestamp_to_parts(ts: float) -> Tuple[int, int]:
    seconds = int(ts)
    fraction = int((ts - seconds) * (2**32))
    return seconds, fraction

def _parts_to_timestamp(seconds: int, fraction: int) -> float:
    return seconds + float(fraction) / (2**32)

def query_ntp_once(server: str, timeout_ms: int = 800) -> NtpResult:
    """
    Perform a single SNTP query and return RTT and clock offset.
    Uses time.perf_counter_ns for high-resolution local elapsed timing.
    """
    first_byte = (0 << 6) | (4 << 3) | 3  # LI=0, VN=4, Mode=3
    packet = bytearray(48)
    packet[0] = first_byte

    # High-resolution local start
    p1_ns = time.perf_counter_ns()
    # System wall clock for NTP timestamps
    t1_sys = time.time()
    t1_ntp = _system_to_ntp_time(t1_sys)
    t1_sec, t1_frac = _timestamp_to_parts(t1_ntp)
    struct.pack_into("!I", packet, 40, t1_sec)
    struct.pack_into("!I", packet, 44, t1_frac)

    addr = (server, NTP_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_ms / 1000.0)
    try:
        sock.sendto(packet, addr)
        data, _ = sock.recvfrom(48)
        p4_ns = time.perf_counter_ns()
        t4_sys = time.time()
    except Exception as e:
        sock.close()
        error = "连接校时服务器超时" if isinstance(e, TimeoutError) else f"无法连接校时服务器：{e}"
        return NtpResult(server, rtt_ms=0, offset_ms=0, success=False, error=error)
    finally:
        sock.close()

    if len(data) < 48:
        return NtpResult(server, rtt_ms=0, offset_ms=0, success=False, error="服务器返回的 NTP 数据不完整")

    unpacked = struct.unpack("!B B b b 11I", data)
    recv_sec = unpacked[9]
    recv_frac = unpacked[10]
    tx_sec = unpacked[11]
    tx_frac = unpacked[12]

    # Server timestamps
    t2_ntp = _parts_to_timestamp(recv_sec, recv_frac)
    t3_ntp = _parts_to_timestamp(tx_sec, tx_frac)
    t2 = _ntp_to_system_time(t2_ntp)
    t3 = _ntp_to_system_time(t3_ntp)

    # High-resolution local round-trip
    local_rtt = (p4_ns - p1_ns) / 1e9  # seconds
    # RFC 5905 formula: rtt = (t4 - t1) - (t3 - t2)
    # Use high-res for (t4-t1) and subtract server delta
    rtt = local_rtt - (t3 - t2)
    if rtt < 0:
        # Guard against slight inconsistencies
        rtt = (t4_sys - t1_sys) - (t3 - t2)
    offset = ((t2 - t1_sys) + (t3 - t4_sys)) / 2.0

    return NtpResult(server, rtt_ms=max(0.0, rtt * 1000.0), offset_ms=offset * 1000.0, success=True)

def query_ntp(server: str, timeout_ms: int = 800, samples: int = 1) -> NtpResult:
    """
    Perform multiple SNTP queries and return median RTT and median offset for stability.
    """
    samples = max(1, int(samples))
    results: List[NtpResult] = []
    last_err: Optional[str] = None
    for _ in range(samples):
        res = query_ntp_once(server, timeout_ms=timeout_ms)
        if res.success:
            results.append(res)
        else:
            last_err = res.error
    if results:
        rtts = [r.rtt_ms for r in results]
        offsets = [r.offset_ms for r in results]
        return NtpResult(server, rtt_ms=float(statistics.median(rtts)),
                         offset_ms=float(statistics.median(offsets)), success=True)
    return NtpResult(server, rtt_ms=0.0, offset_ms=0.0, success=False, error=last_err or "NTP 校时请求失败")

def _icmp_ping_windows(host: str, timeout_ms: int = 800, samples: int = 3) -> Optional[float]:
    """
    Best-effort ICMP ping via Windows 'ping' command (no admin needed).
    Uses multiple samples and returns the median RTT in ms, or None on failure.
    """
    try:
        proc = subprocess.run(
            ["ping", "-n", str(max(1, int(samples))), "-w", str(max(1, int(timeout_ms))), host],
            capture_output=True, text=True, timeout=(timeout_ms / 1000.0) * samples + 2
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        times = [int(m.group(1)) for m in re.finditer(r"time[=<]\s*(\d+)\s*ms", out, re.IGNORECASE)]
        if times:
            return float(statistics.median(times))
        # Fallback: parse "Average = Xms"
        m2 = re.search(r"Average\s*=\s*(\d+)\s*ms", out, re.IGNORECASE)
        if m2:
            return float(m2.group(1))
    except Exception:
        pass
    return None

def ping_servers(
    servers: List[str],
    timeout_ms: int = 800,
    max_workers: int = 8,
    samples: int = 3,
    progress_cb: Optional[Callable[[int, int], None]] = None
) -> List[NtpResult]:
    """
    Ping multiple servers in parallel (SNTP median). If SNTP fails, fall back to ICMP median.
    Returns results in the same order as input.
    Reports progress via progress_cb(done, total) as each server finishes.
    """
    results: List[Optional[NtpResult]] = [None] * len(servers)
    threads: List[threading.Thread] = []
    sem = threading.Semaphore(max_workers)
    done_count = 0
    lock = threading.Lock()
    total = len(servers)

    def worker(idx: int, server: str):
        nonlocal done_count
        with sem:
            ntp_res = query_ntp(server, timeout_ms=timeout_ms, samples=samples)
            if ntp_res.success:
                results[idx] = ntp_res
            else:
                icmp_rtt = _icmp_ping_windows(server, timeout_ms=timeout_ms, samples=max(2, samples))
                if icmp_rtt is not None:
                    results[idx] = NtpResult(server, rtt_ms=icmp_rtt, offset_ms=0.0, success=False, error="已改用 ICMP 测试延迟")
                else:
                    results[idx] = NtpResult(server, rtt_ms=0.0, offset_ms=0.0, success=False, error=ntp_res.error or "NTP 校时请求失败")
        with lock:
            done_count += 1
            if progress_cb:
                try:
                    progress_cb(done_count, total)
                except Exception:
                    pass

    for i, s in enumerate(servers):
        t = threading.Thread(target=worker, args=(i, s), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return [r if r is not None else NtpResult(servers[i], 0.0, 0.0, False, "原因未知") for i, r in enumerate(results)]
