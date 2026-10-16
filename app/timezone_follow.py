"""Resolve one verified exit IP by consensus, then follow it with a stable check."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time

import requests
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from app import time_utils


@dataclass(frozen=True)
class GeoResult:
    ip: str
    country: str
    iana: str
    windows: str
    sources: tuple
    details: tuple


def choose_consensus(ip, country, observations):
    """Require two matching Windows zones and a strict majority of valid votes."""
    votes = []
    details = []
    seen = set()
    for observation in observations:
        source = observation["source"]
        if source in seen:
            continue
        seen.add(source)
        try:
            if observation.get("error"):
                raise ValueError(observation["error"])
            if ipaddress.ip_address(observation.get("ip", "")) != ipaddress.ip_address(ip):
                raise ValueError("返回的 IP 与待查询出口不一致")
            if observation.get("country", "").upper() != country.upper():
                raise ValueError("国家或地区与出口检测结果不一致")
            iana = observation.get("iana", "")
            windows = time_utils.iana_to_windows(iana)
            votes.append((source, iana, windows))
            details.append(f"{source}：{iana}")
        except ValueError as error:
            details.append(f"{source}：已排除（{error}）")
    counts = Counter(vote[2] for vote in votes)
    if counts:
        winner, count = counts.most_common(1)[0]
        if count >= 2 and count > len(votes) / 2:
            agreed = [vote for vote in votes if vote[2] == winner]
            return GeoResult(ip, country, agreed[0][1], winner,
                             tuple(vote[0] for vote in agreed), tuple(details))
    raise ValueError("查询来源未形成至少两票的明确共识；保留当前时区。\n" + "\n".join(details))


class GeoResolver:
    PROVIDERS = {
        "ipwho.is": "https://ipwho.is/{ip}",
        "ipinfo.io": "https://ipinfo.io/{ip}/json",
        "ipapi.co": "https://ipapi.co/{ip}/json/",
    }

    def __init__(self, get=None, clock=None):
        self.get = get or requests.get
        self.clock = clock or time.monotonic
        self._cached = None
        self._failure = None

    def _request(self, url):
        response = self.get(url, timeout=(3, 5), headers={"User-Agent": "TimeGuardian/1.1"})
        response.raise_for_status()
        return response

    def probe_exit(self):
        response = self._request("https://www.cloudflare.com/cdn-cgi/trace")
        data = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line)
        ip = str(ipaddress.ip_address(data.get("ip", "")))
        country = data.get("loc", "").upper()
        if not ipaddress.ip_address(ip).is_global or len(country) != 2 or country == "XX":
            raise ValueError("未能确认有效的公网出口和国家或地区。")
        return ip, country

    def _observe(self, source, url, ip):
        try:
            data = self._request(url.format(ip=ip)).json()
            zone = data.get("timezone")
            if isinstance(zone, dict):
                zone = zone.get("id")
            country = data.get("country_code") or data.get("country") or ""
            return {"source": source, "ip": data.get("ip", ""),
                    "country": country, "iana": zone or ""}
        except (requests.RequestException, ValueError, TypeError) as error:
            return {"source": source, "error": f"查询失败（{type(error).__name__}）"}

    def lookup(self, force=False):
        identity = self.probe_exit()
        now = self.clock()
        if not force and self._cached and self._cached[0] == identity and now < self._cached[1]:
            result = self._cached[2]
        else:
            if not force and self._failure and self._failure[0] == identity and now < self._failure[1]:
                raise ValueError(self._failure[2])
            with ThreadPoolExecutor(max_workers=len(self.PROVIDERS)) as pool:
                futures = [pool.submit(self._observe, source, url, identity[0])
                           for source, url in self.PROVIDERS.items()]
                observations = [future.result() for future in as_completed(futures)]
            # Sort for deterministic diagnostics, independent of response speed.
            observations.sort(key=lambda item: item["source"])
            try:
                result = choose_consensus(*identity, observations)
            except ValueError as error:
                self._failure = (identity, now + 300, str(error))
                raise
            self._cached = (identity, now + 21600, result)
            self._failure = None
        if self.probe_exit() != identity:
            self._cached = None
            raise ValueError("查询过程中出口发生变化，等待下一轮重新确认。")
        return result


class TimezoneFollower(QObject):
    status_changed = Signal(str)
    notice = Signal(str, bool)
    completed = Signal(int, bool, object, str)

    def __init__(self, cfg, parent=None, resolver=None):
        super().__init__(parent)
        self.cfg = cfg
        self.resolver = resolver or GeoResolver()
        self.status = "尚未检查"
        self.last_result = None
        self._generation = 0
        self._enabled = False
        self._busy = False
        self._pending = None
        self._confirmations = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.request_check)
        self.completed.connect(self._finish)
        self.log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TimeGuardian"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("TimeGuardian.timezone")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = RotatingFileHandler(self.log_dir / "timezone-follow.log", maxBytes=524288,
                                          backupCount=2, encoding="utf-8")
            formatter = logging.Formatter("%(asctime)sZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
            formatter.converter = time.gmtime
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def configure(self):
        enabled = bool(self.cfg.settings.auto_timezone_enabled)
        changed = enabled != self._enabled
        if changed:
            self._generation += 1
            self._pending = None
            self._confirmations = 0
        self._enabled = enabled
        if enabled:
            interval = max(30, min(3600, int(self.cfg.settings.timezone_check_interval_sec)))
            self.timer.start(interval * 1000)
            if changed:
                self._set_status("正在确认出口时区")
                QTimer.singleShot(0, self.request_check)
        else:
            self.timer.stop()
            self._set_status("自动跟随已关闭")

    def stop(self):
        self._generation += 1
        self._enabled = False
        self.timer.stop()

    def request_check(self, manual=False):
        if self._busy or (not manual and not self._enabled):
            return
        self._busy = True
        generation = self._generation
        def work():
            try:
                result = self.resolver.lookup(force=manual)
                self.completed.emit(generation, manual, result, "")
            except Exception as error:
                self.completed.emit(generation, manual, None, f"出口时区检查失败：{error}")
        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, status):
        if status != self.status:
            self.status = status
            self.status_changed.emit(status)
            self.logger.info(status)
        record = {"updated_at": datetime.now(timezone.utc).isoformat(), "status": status,
                  "auto_enabled": self._enabled,
                  "result": asdict(self.last_result) if self.last_result else None}
        try:
            target = self.log_dir / "timezone-follow-status.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            self.logger.exception("无法写入时区跟随状态记录")

    @Slot(int, bool, object, str)
    def _finish(self, generation, manual, result, error):
        self._busy = False
        if generation != self._generation:
            if self._enabled:
                QTimer.singleShot(0, self.request_check)
            return
        if error:
            self.last_result = None
            self._pending = None
            self._confirmations = 0
            changed = error != self.status
            self._set_status(error)
            if manual or changed:
                self.notice.emit(error, True)
            return
        self.last_result = result
        current = time_utils.get_current_timezone()
        if not current:
            self._pending = None
            self._confirmations = 0
            self._set_status("无法读取当前系统时区，暂不切换。")
            return
        if current == result.windows:
            self._pending = None
            self._confirmations = 0
            self._set_status(f"已匹配出口：{result.iana}（{result.ip}）")
            if manual:
                self.notice.emit("当前系统时区已与出口一致，无需调整。", False)
            return
        candidate = (result.ip, result.windows)
        self._confirmations = self._confirmations + 1 if self._pending == candidate else 1
        self._pending = candidate
        if not manual and self._confirmations < 2:
            self._set_status(f"等待下一轮确认（1/2）：{result.iana}（{result.ip}）")
            return
        ok, message = time_utils.set_windows_timezone(result.windows)
        if ok:
            self._pending = None
            self._confirmations = 0
            self._set_status(f"已跟随出口：{result.iana}（{result.ip}）")
            self.logger.info("时区切换 %s -> %s；查询证据：%s", current, result.windows, asdict(result))
            self.notice.emit(f"已跟随出口切换时区。{message}", False)
        else:
            changed = message != self.status
            self._set_status(message)
            if manual or changed:
                self.notice.emit(message, True)
