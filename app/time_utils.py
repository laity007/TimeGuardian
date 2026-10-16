import ctypes
import datetime
import os
import struct
import subprocess
import sys
from typing import List, Optional, Tuple, Dict, Set
from PySide6.QtCore import QTimeZone


# Windows SYSTEMTIME for SetSystemTime (UTC)
class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", ctypes.c_ushort),
        ("wMonth", ctypes.c_ushort),
        ("wDayOfWeek", ctypes.c_ushort),
        ("wDay", ctypes.c_ushort),
        ("wHour", ctypes.c_ushort),
        ("wMinute", ctypes.c_ushort),
        ("wSecond", ctypes.c_ushort),
        ("wMilliseconds", ctypes.c_ushort),
    ]

def is_windows() -> bool:
    return os.name == "nt"

def set_system_time_utc_via_helper(target_epoch_ms: int, allow_elevation: bool = False) -> Tuple[bool, str]:
    from elevated_time_setter import set_system_time_utc
    try:
        if set_system_time_utc(target_epoch_ms):
            return True, "系统时间已校准。"
    except (OSError, ValueError, OverflowError) as error:
        return False, f"校时失败：{error}"
    if not allow_elevation:
        return False, "自动校时未执行：当前权限不足。可使用 Windows 自动校时，或主动点击“立即校时”授权；后台不会弹出权限请求。"
    if getattr(sys, "frozen", False):
        return False, "当前打包版本无法单独启动校时辅助程序，请使用 Windows 设置中的“立即同步”。"
    helper = os.path.join(os.path.dirname(sys.argv[0]), "elevated_time_setter.py")
    if not os.path.isfile(helper):
        candidate = os.path.join(os.path.dirname(__file__), "..", "elevated_time_setter.py")
        candidate = os.path.abspath(candidate)
        if os.path.isfile(candidate):
            helper = candidate
    if not os.path.isfile(helper):
        return False, "未找到校时所需的管理员权限辅助程序。"
    python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
    arguments = subprocess.list2cmdline([helper, "--set-utc-epoch-ms", str(target_epoch_ms)])
    def ps_quote(value):
        return "'" + value.replace("'", "''") + "'"
    cmd = [
        "powershell",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        "$ErrorActionPreference = 'Stop'; try { "
        f"$timeGuardianHelper = Start-Process -FilePath {ps_quote(python_exe)} "
        f"-ArgumentList {ps_quote(arguments)} -Verb RunAs -WindowStyle Hidden -PassThru -Wait; "
        "exit $timeGuardianHelper.ExitCode } catch { "
        "if ($_.Exception.NativeErrorCode -eq 1223) { exit 1223 }; exit 1 }"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            return True, "系统时间已校准。"
        if result.returncode == 1223:
            return False, "已取消校时授权，后台不会重试提权。"
        return False, "未能完成授权校时，请确认是否取消了权限请求，或使用 Windows 设置中的“立即同步”。"
    except subprocess.TimeoutExpired:
        return False, "请求管理员权限超时，请重试。"
    except Exception as e:
        return False, f"无法请求管理员权限： {e}"

def list_windows_timezones() -> List[str]:
    try:
        out = subprocess.check_output(["tzutil", "/l"], text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []

def get_current_timezone() -> str:
    try:
        out = subprocess.check_output(["tzutil", "/g"], text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        return out.strip()
    except Exception:
        return ""

def set_windows_timezone(tz_standard_name: str) -> Tuple[bool, str]:
    try:
        subprocess.check_call(["tzutil", "/s", tz_standard_name], timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if get_current_timezone() != tz_standard_name:
            return False, "系统未确认时区变更，请检查权限或 Windows 时区策略。"
        return True, f"已将系统时区设为 {timezone_display_name(tz_standard_name)}。"
    except subprocess.CalledProcessError as e:
        return False, f"设置时区失败： {e}"
    except Exception as e:
        return False, f"设置时区时发生异常： {e}"


def timezone_display_name(key: str) -> str:
    for zone in _enum_windows_time_zones_registry():
        if zone["key"] == key:
            return zone["display"] or key
    return key

def _format_utc_offset(minutes: int, with_space: bool = True) -> str:
    sign = "+" if minutes >= 0 else "-"
    m = abs(minutes)
    hh = m // 60
    mm = m % 60
    return f"UTC {sign}{hh:02d}:{mm:02d}" if with_space else f"UTC{sign}{hh:02d}:{mm:02d}"

def _parse_display_utc_prefix(display: str) -> Optional[int]:
    if not display.startswith("(UTC") or ")" not in display:
        return None
    try:
        prefix = display[1:display.index(")")]  # e.g., 'UTC+03:30'
        if not prefix.startswith("UTC"):
            return None
        s = prefix[3:]
        sign = 1 if s[0] == "+" else -1
        s = s[1:]
        if ":" in s:
            hh, mm = s.split(":", 1)
        else:
            hh = s[:2]
            mm = s[2:4] if len(s) >= 4 else "00"
        return sign * (int(hh) * 60 + int(mm))
    except Exception:
        return None

def _enum_windows_time_zones_registry() -> List[Dict]:
    zones: List[Dict] = []
    if not is_windows():
        return zones
    try:
        import winreg  # type: ignore
        base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Time Zones"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ) as root:
            i = 0
            while True:
                try:
                    key_name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, key_name, 0, winreg.KEY_READ) as k:
                        try:
                            display, _ = winreg.QueryValueEx(k, "Display")
                        except Exception:
                            display = ""
                        try:
                            resource, _ = winreg.QueryValueEx(k, "MUI_Display")
                            localized = ctypes.create_unicode_buffer(1024)
                            if ctypes.windll.shlwapi.SHLoadIndirectString(resource, localized, len(localized), None) == 0 and localized.value:
                                display = localized.value
                        except (OSError, AttributeError):
                            pass
                        try:
                            tzi_bytes, _ = winreg.QueryValueEx(k, "TZI")
                            bias, std_bias, dlt_bias, *rest = struct.unpack("<3l8H8H", tzi_bytes)
                            daylight_month = rest[8]
                            standard_month = rest[0]
                            has_dst = (daylight_month != 0 or standard_month != 0)
                            offset_minutes = -(bias)
                        except Exception:
                            offset_minutes = _parse_display_utc_prefix(display) or 0
                            has_dst = False
                        zones.append({
                            "key": key_name,
                            "display": display,
                            "offset_minutes": offset_minutes,
                            "has_dst": has_dst
                        })
                except Exception:
                    continue
    except Exception:
        pass
    return zones

WIN_TO_IANA: Dict[str, str] = {
    zone["key"]: bytes(QTimeZone.windowsIdToDefaultIanaId(zone["key"].encode("utf-8"))).decode("utf-8")
    for zone in _enum_windows_time_zones_registry()
}


def iana_to_windows(iana: str) -> str:
    aliases = {"US/Pacific": "America/Los_Angeles", "US/Eastern": "America/New_York", "UTC": "Etc/UTC"}
    encoded = aliases.get(iana, iana).encode("utf-8")
    key = bytes(QTimeZone.ianaIdToWindowsId(encoded)).decode("utf-8")
    if not QTimeZone(encoded).isValid() or not key or key not in WIN_TO_IANA:
        raise ValueError(f"无法将 {iana} 映射到本机支持的 Windows 时区。")
    return key

def windows_key_to_iana(key: str) -> Optional[str]:
    return WIN_TO_IANA.get(key)

def iana_city_region(iana: str) -> Tuple[str, str]:
    if "/" in iana:
        area, city = iana.split("/", 1)
        return city.replace("_", " "), area
    return iana, ""

def list_timezone_choices_labels() -> List[Tuple[str, str]]:
    zones = _enum_windows_time_zones_registry()
    items: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for z in zones:
        key = z["key"]
        off = z["offset_minutes"]
        label_off = _format_utc_offset(off)
        disp = z["display"]
        city_part = disp.split(")", 1)[1].strip() if ")" in disp else disp
        label = f"(UTC {label_off[4:]}) {city_part}"
        if key not in seen:
            items.append((label, key))
            seen.add(key)
    def offset_for_key(k: str) -> int:
        for z in zones:
            if z["key"] == k:
                return z["offset_minutes"]
        return 0
    items.sort(key=lambda x: (offset_for_key(x[1]), x[0]))
    return items

def get_timezone_from_ip_modern(timeout_sec: float = 5.0, overall_timeout_sec: float = 8.0) -> Tuple[Optional[str], Optional[int]]:
    # Compatibility entry point; all IP-based operations now require consensus.
    from app.timezone_follow import GeoResolver
    from PySide6.QtCore import QDateTime
    result = GeoResolver().lookup()
    zone = QTimeZone(result.iana.encode("utf-8"))
    return result.iana, zone.offsetFromUtc(QDateTime.currentDateTimeUtc()) // 60


def get_timezone_offset_from_ip(timeout_sec: float = 5.0) -> Optional[int]:
    iana, off = get_timezone_from_ip_modern(timeout_sec=timeout_sec)
    return off

def _best_zone_for_offset(offset_minutes: int) -> Optional[str]:
    zones = _enum_windows_time_zones_registry()
    candidates_utc = [z for z in zones if (not z["has_dst"]) and z["offset_minutes"] == offset_minutes and z["key"].upper().startswith("UTC")]
    if candidates_utc:
        return sorted((z["key"] for z in candidates_utc))[0]
    candidates = [z for z in zones if (not z["has_dst"]) and z["offset_minutes"] == offset_minutes]
    if candidates:
        return sorted((z["key"] for z in candidates))[0]
    any_candidates = [z for z in zones if z["offset_minutes"] == offset_minutes]
    if any_candidates:
        return sorted((z["key"] for z in any_candidates))[0]
    return None

def set_timezone_by_utc_label(label: str) -> Tuple[bool, str]:
    if not label.upper().startswith("UTC"):
        return False, "时区偏移必须以 UTC 开头。"
    try:
        s = label[3:].strip()
        sign = 1 if s[0] == "+" else -1
        hh, mm = s[1:].split(":")
        minutes = sign * (int(hh) * 60 + int(mm))
    except Exception:
        return False, "时区偏移格式无效，请使用 UTC+08:00 这样的格式。"
    key = _best_zone_for_offset(minutes)
    if not key:
        return False, f"未找到与 UTC{label[3:]} 对应的 Windows 时区。"
    return set_windows_timezone(key)

def set_region_from_country_code(country_code: str) -> Tuple[bool, str]:
    ps_script = r"""
$ErrorActionPreference = 'Stop'
function Try-SetHomeLocation([string]$CountryCode) {
  try {
    if (Get-Command -Name Set-WinHomeLocation -ErrorAction SilentlyContinue) {
      $geo = Get-WinHomeLocation -ListAvailable | Where-Object { $_.CountryCode -eq $CountryCode }
      if ($geo) {
        Set-WinHomeLocation -GeoId $geo.GeoId
        return $true
      }
    }
  } catch { }
  return $false
}
function Try-SetCulture([string]$CountryCode) {
  try {
    if (Get-Command -Name Set-Culture -ErrorAction SilentlyContinue) {
      $candidate = "en-$CountryCode"
      try {
        Set-Culture -CultureInfo $candidate
        return $true
      } catch {
        $cult = [System.Globalization.CultureInfo]::GetCultures([System.Globalization.CultureTypes]::AllCultures) |
          Where-Object { $_.Name -match "-$CountryCode$" } | Select-Object -First 1
        if ($cult) {
          Set-Culture -CultureInfo $cult.Name
          return $true
        }
      }
    }
  } catch { }
  return $false
}
$cc = "%CC%"
$ok1 = Try-SetHomeLocation $cc
$ok2 = Try-SetCulture $cc
if ($ok1 -or $ok2) { exit 0 } else { exit 1 }
"""
    ps_script = ps_script.replace("%CC%", country_code.upper())
    try:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".ps1", encoding="utf-8") as tf:
            tf.write(ps_script)
            script_path = tf.name
    except Exception as e:
        return False, f"无法创建调整地区设置所需的临时脚本： {e}"
    cmd = [
        "powershell",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        f"Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"{script_path}\"' -Verb RunAs -WindowStyle Hidden"
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return False, "调整地区设置时，请求管理员权限超时。"
    except Exception as e:
        return False, f"无法请求管理员权限： {e}"
    return True, "已请求调整 Windows 地区和区域格式，请在系统设置中确认结果。"

def register_startup(app_name: str, exe_path: str, enable: bool) -> Tuple[bool, str]:
    """
    Adds or removes a Run registry key for current user (no admin required).
    Writes a --startup flag to allow the app to detect startup launches and suppress the welcome dialog.
    """
    if not is_windows():
        return False, "登录自启动仅支持 Windows。"
    try:
        import winreg  # type: ignore
    except Exception:
        return False, "无法使用 Windows 注册表组件，未能设置自启动。"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_ALL_ACCESS) as key:
            if enable:
                if getattr(sys, "frozen", False):
                    value = subprocess.list2cmdline([exe_path, "--startup"])
                else:
                    pythonw = os.path.join(os.path.dirname(exe_path), "pythonw.exe")
                    interpreter = pythonw if os.path.isfile(pythonw) else exe_path
                    launcher = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "launch.py"))
                    value = subprocess.list2cmdline([interpreter, launcher, "--startup"])
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, value)
                return True, "已开启登录自启动。"
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
                return True, "已关闭登录自启动。"
    except Exception as e:
        return False, f"更新自启动设置失败： {e}"
