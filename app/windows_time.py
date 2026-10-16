"""Query Windows Time without elevation; enable it only on an explicit request."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import uuid
import threading
from PySide6.QtCore import QObject, Signal, Slot


def service_running():
    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "service_type", "current_state", "controls", "win32_exit", "specific_exit", "checkpoint", "wait_hint")]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    advapi.OpenSCManagerW.restype = wintypes.HANDLE
    advapi.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    advapi.OpenServiceW.restype = wintypes.HANDLE
    advapi.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
    advapi.QueryServiceStatus.restype = wintypes.BOOL
    advapi.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    advapi.CloseServiceHandle.restype = wintypes.BOOL
    manager = advapi.OpenSCManagerW(None, None, 1)
    if not manager:
        return False
    service = None
    try:
        service = advapi.OpenServiceW(manager, "W32Time", 4)
        if not service:
            return False
        status = SERVICE_STATUS()
        return bool(advapi.QueryServiceStatus(service, ctypes.byref(status))) and status.current_state == 4
    finally:
        if service:
            advapi.CloseServiceHandle(service)
        advapi.CloseServiceHandle(manager)


def enable_service():
    script = Path(__file__).resolve().parents[1] / "scripts" / "Enable-WindowsTime.ps1"
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TimeGuardian"
    log_dir.mkdir(parents=True, exist_ok=True)
    result_path = log_dir / f"windows-time-setup-{uuid.uuid4().hex}.json"
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    arguments = subprocess.list2cmdline(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-ResultPath", str(result_path)])
    command = ("$ErrorActionPreference='Stop'; try { "
               f"$timeSetup = Start-Process powershell.exe -ArgumentList {quote(arguments)} "
               "-Verb RunAs -WindowStyle Hidden -PassThru -Wait; exit $timeSetup.ExitCode "
               "} catch { if ($_.Exception.NativeErrorCode -eq 1223 -or $_.Exception.InnerException.NativeErrorCode -eq 1223) { exit 1223 }; exit 1 }")
    try:
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                   capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode == 1223:
            return False, "已取消启用 Windows 自动校时；不会自动重试授权。"
        if not result_path.exists():
            return False, "未收到 Windows 自动校时的配置结果，请重试。"
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        if result.get("Configured") and result.get("ResyncRequested"):
            return True, "已启用 Windows 自动校时并请求同步。后续由系统服务校准，无需常驻程序提权；偏差可能逐步收敛。"
        return False, "Windows 自动校时尚未配置完成。详情：" + result.get("Message", "原因未知")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return False, f"启用 Windows 自动校时未完成：{error}"


class ServiceSetupController(QObject):
    completed = Signal(bool, str)
    finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.busy = False
        self.completed.connect(self._finish)

    def start(self):
        if self.busy:
            return
        self.busy = True
        def work():
            try:
                ok, message = enable_service()
            except Exception as error:
                ok, message = False, f"启用 Windows 自动校时未完成：{error}"
            self.completed.emit(ok, message)
        threading.Thread(target=work, daemon=True).start()

    @Slot(bool, str)
    def _finish(self, ok, message):
        self.busy = False
        self.finished.emit(ok, message)
