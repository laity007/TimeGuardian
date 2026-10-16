"""Run the source application independently of the caller's working directory."""
import os
from pathlib import Path
import runpy
import sys
import traceback

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    instance_mutex = kernel.CreateMutexW(None, False, "Local\\TimeGuardianSource")
    if not instance_mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        sys.exit(0)

root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))

try:
    runpy.run_module("app.main", run_name="__main__")
except Exception:
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(root))) / "TimeGuardian"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "startup-error.log").open("a", encoding="utf-8") as log:
        traceback.print_exc(file=log)
    raise
