# Elevated helper to set system UTC time. Must be run as Administrator.

import argparse
import ctypes
import datetime
from ctypes import wintypes
import sys

# Windows SYSTEMTIME structure
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

def dt_to_systemtime(dt: datetime.datetime) -> SYSTEMTIME:
    return SYSTEMTIME(
        dt.year, dt.month, dt.weekday(), dt.day,
        dt.hour, dt.minute, dt.second, int(dt.microsecond / 1000)
    )

def enable_set_time_privilege() -> bool:
    """
    Enable SeSystemtimePrivilege for this process token. Requires elevation.
    """
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    SE_PRIVILEGE_ENABLED = 0x00000002
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_uint32)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", ctypes.c_uint32), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
                                             wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)):
        return False

    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeSystemtimePrivilege", ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        ctypes.set_last_error(0)
        ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        # The API can return success together with ERROR_NOT_ALL_ASSIGNED.
        return bool(ok) and ctypes.get_last_error() == 0
    finally:
        kernel32.CloseHandle(token)

def set_system_time_utc(epoch_ms: int) -> bool:
    """
    Sets the system time to the specified UTC epoch milliseconds.
    """
    if not enable_set_time_privilege():
        return False
    dt = datetime.datetime.fromtimestamp(epoch_ms / 1000.0, datetime.timezone.utc).replace(tzinfo=None)
    st = dt_to_systemtime(dt)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetSystemTime.argtypes = [ctypes.POINTER(SYSTEMTIME)]
    kernel32.SetSystemTime.restype = wintypes.BOOL
    ok = kernel32.SetSystemTime(ctypes.byref(st))
    return ok != 0

def main():
    parser = argparse.ArgumentParser(description="以管理员权限校准 Windows 系统时间。")
    parser.add_argument("--set-utc-epoch-ms", type=int, required=True, help="目标 UTC 时间，以 Unix 时间戳的毫秒数表示。")
    args = parser.parse_args()

    if set_system_time_utc(args.set_utc_epoch_ms):
        sys.exit(0)
    else:
        sys.exit(2)

if __name__ == "__main__":
    main()
