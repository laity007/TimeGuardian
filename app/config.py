import json
import os
from dataclasses import dataclass, asdict, field
from typing import List, Dict

APP_NAME = "TimeGuardian"

DEFAULT_SERVERS = [
    "time.google.com",
    "time.cloudflare.com",
    "time.windows.com",
    "pool.ntp.org",
    "europe.pool.ntp.org",
    "asia.pool.ntp.org",
    "north-america.pool.ntp.org",
    "south-america.pool.ntp.org",
    "oceania.pool.ntp.org",
]

def appdata_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path

def config_path() -> str:
    return os.path.join(appdata_dir(), "config.json")

@dataclass
class Settings:
    # Auto sync controls
    auto_sync_enabled: bool = True
    # Periodic check interval (seconds)
    check_interval_sec: int = 300  # 5 minutes
    # Maximum allowed drift (milliseconds) before applying a correction
    drift_threshold_ms: int = 200
    # NTP servers to probe
    ntp_servers: List[str] = field(default_factory=lambda: list(DEFAULT_SERVERS))
    # Per-probe timeout (milliseconds)
    ntp_timeout_ms: int = 800
    # Auto vs manual server
    auto_select_server: bool = True
    preferred_server: str = ""
    # Launch at login
    launch_at_startup: bool = False
    # Optional features
    allow_timezone_from_ip: bool = False
    auto_timezone_enabled: bool = False
    timezone_check_interval_sec: int = 60
    allow_region_from_ip: bool = False
    # UI/UX
    show_welcome_on_launch: bool = True  # show the help dialog on manual launch
    notifications_enabled: bool = True   # allow tray notifications (balloons)
    # Logging preferences
    log_level: str = "INFO"
    # Connected state TTL (seconds) for tray icon
    connected_status_ttl_sec: int = 600
    # Last ping results (server -> rtt_ms); informational only
    last_ping_results: Dict[str, float] = field(default_factory=dict)

class Config:
    def __init__(self):
        self.settings = Settings()
        self._path = config_path()
        self.load()

    def load(self):
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    merged = asdict(Settings())
                    merged.update(data)
                    self.settings = Settings(**merged)
            except Exception:
                pass

    def save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(asdict(self.settings), f, indent=2)
        except Exception:
            pass
