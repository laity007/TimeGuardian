import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import windows_time


class WindowsTimeTests(unittest.TestCase):
    def test_service_query_closes_both_handles(self):
        advapi = Mock()
        advapi.OpenSCManagerW.return_value = 101
        advapi.OpenServiceW.return_value = 202
        def query(handle, status):
            status._obj.current_state = 4
            return True
        advapi.QueryServiceStatus.side_effect = query
        with patch.object(windows_time.ctypes, "WinDLL", return_value=advapi):
            self.assertTrue(windows_time.service_running())
        self.assertEqual([call.args[0] for call in advapi.CloseServiceHandle.call_args_list], [202, 101])

    def test_service_query_access_failure_is_not_running(self):
        advapi = Mock()
        advapi.OpenSCManagerW.return_value = 101
        advapi.OpenServiceW.return_value = 0
        with patch.object(windows_time.ctypes, "WinDLL", return_value=advapi):
            self.assertFalse(windows_time.service_running())
        advapi.CloseServiceHandle.assert_called_once_with(101)

    def test_explicit_setup_success_requires_persisted_result(self):
        with tempfile.TemporaryDirectory(prefix="time-setup-") as directory:
            def run(command, **kwargs):
                result = Path(directory) / "TimeGuardian" / "windows-time-setup-test.json"
                result.write_text(json.dumps({"Configured": True, "ResyncRequested": True}), encoding="utf-8-sig")
                self.assertIn("-Verb RunAs", command[-1])
                self.assertIn("-WindowStyle Hidden", command[-1])
                self.assertIn("Enable-WindowsTime.ps1", command[-1])
                return SimpleNamespace(returncode=0)
            with patch.dict(windows_time.os.environ, {"LOCALAPPDATA": directory}), \
                 patch.object(windows_time.uuid, "uuid4", return_value=SimpleNamespace(hex="test")), \
                 patch.object(windows_time.subprocess, "run", side_effect=run):
                ok, message = windows_time.enable_service()
            self.assertTrue(ok)
            self.assertIn("请求同步", message)

    def test_rejected_setup_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(windows_time.os.environ, {"LOCALAPPDATA": directory}), \
             patch.object(windows_time.subprocess, "run", return_value=SimpleNamespace(returncode=1223)) as run:
            ok, message = windows_time.enable_service()
        self.assertFalse(ok)
        self.assertIn("取消", message)
        run.assert_called_once()

    def test_partial_configuration_is_not_reported_as_sync_success(self):
        with tempfile.TemporaryDirectory() as directory:
            def run(*args, **kwargs):
                result = Path(directory) / "TimeGuardian" / "windows-time-setup-test.json"
                result.write_text(json.dumps({"Configured": True, "ResyncRequested": False, "Message": "network"}), encoding="utf-8")
                return SimpleNamespace(returncode=1)
            with patch.dict(windows_time.os.environ, {"LOCALAPPDATA": directory}), \
                 patch.object(windows_time.uuid, "uuid4", return_value=SimpleNamespace(hex="test")), \
                 patch.object(windows_time.subprocess, "run", side_effect=run):
                ok, message = windows_time.enable_service()
            self.assertFalse(ok)
            self.assertIn("network", message)


if __name__ == "__main__":
    unittest.main()
