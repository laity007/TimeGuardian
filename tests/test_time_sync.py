import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import elevated_time_setter as helper
from app import main, ntp_client, time_utils
from app.config import Settings


class PrivilegeTests(unittest.TestCase):
    def test_not_all_privileges_assigned_is_failure_and_handle_is_closed(self):
        advapi, kernel = Mock(), Mock()
        advapi.OpenProcessToken.return_value = 1
        advapi.LookupPrivilegeValueW.return_value = 1
        advapi.AdjustTokenPrivileges.return_value = 1
        with patch.object(helper.ctypes, "WinDLL", side_effect=[advapi, kernel]), \
             patch.object(helper.ctypes, "set_last_error"), \
             patch.object(helper.ctypes, "get_last_error", return_value=1300):
            self.assertFalse(helper.enable_set_time_privilege())
        kernel.CloseHandle.assert_called_once()

    def test_no_set_system_time_call_without_privilege(self):
        with patch.object(helper, "enable_set_time_privilege", return_value=False), \
             patch.object(helper.ctypes, "WinDLL") as dll:
            self.assertFalse(helper.set_system_time_utc(1_000_000))
            dll.assert_not_called()


class ElevationTests(unittest.TestCase):
    def test_background_correction_never_starts_an_elevated_process(self):
        with patch.object(helper, "set_system_time_utc", return_value=False), \
             patch.object(time_utils.subprocess, "run") as run:
            for _ in range(3):
                ok, message = time_utils.set_system_time_utc_via_helper(1_000_000)
                self.assertFalse(ok)
                self.assertIn("后台不会", message)
            run.assert_not_called()

    def test_existing_privilege_needs_no_elevation(self):
        with patch.object(helper, "set_system_time_utc", return_value=True), \
             patch.object(time_utils.subprocess, "run") as run:
            self.assertTrue(time_utils.set_system_time_utc_via_helper(1_000_000)[0])
            run.assert_not_called()

    def test_explicit_manual_request_quotes_paths_and_reports_cancellation(self):
        with patch.object(helper, "set_system_time_utc", return_value=False), \
             patch.object(time_utils.os.path, "isfile", return_value=True), \
             patch.object(time_utils.sys, "executable", r"C:\Example App\pythonw.exe"), \
             patch.object(time_utils.sys, "argv", [r"C:\Example App\launch.py"]), \
             patch.object(time_utils.subprocess, "run", return_value=SimpleNamespace(returncode=1223)) as run:
            ok, message = time_utils.set_system_time_utc_via_helper(1_000_000, allow_elevation=True)
            self.assertFalse(ok)
            self.assertIn("取消", message)
            command = run.call_args.args[0][-1]
            self.assertIn('"C:\\Example App\\elevated_time_setter.py"', command)
            self.assertIn("-PassThru -Wait", command)
            # A later automatic cycle must not repeat the user's rejected request.
            time_utils.set_system_time_utc_via_helper(1_000_000)
            self.assertEqual(run.call_count, 1)

    def test_nonzero_helper_exit_is_not_reported_as_success(self):
        with patch.object(helper, "set_system_time_utc", return_value=False), \
             patch.object(time_utils.os.path, "isfile", return_value=True), \
             patch.object(time_utils.subprocess, "run", return_value=SimpleNamespace(returncode=2)):
            self.assertFalse(time_utils.set_system_time_utc_via_helper(1_000_000, allow_elevation=True)[0])


class NtpPacketTests(unittest.TestCase):
    def test_receive_and_transmit_timestamps_produce_correct_offset(self):
        packet = bytearray(48)
        packet[0], packet[1] = 0x24, 1
        for offset, timestamp in [(24, 1000.0), (32, 1000.3), (40, 1000.32)]:
            seconds, fraction = ntp_client._timestamp_to_parts(ntp_client._system_to_ntp_time(timestamp))
            struct.pack_into("!II", packet, offset, seconds, fraction)
        sock = Mock()
        sock.recvfrom.return_value = (bytes(packet), ("8.8.8.8", 123))
        with patch.object(ntp_client.socket, "socket", return_value=sock), \
             patch.object(ntp_client.time, "time", side_effect=[1000.0, 1000.12]), \
             patch.object(ntp_client.time, "perf_counter_ns", side_effect=[0, 120_000_000]):
            result = ntp_client.query_ntp_once("test.invalid")
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.offset_ms, 250, delta=0.001)
        self.assertAlmostEqual(result.rtt_ms, 100, delta=0.001)


class SyncManagerTests(unittest.TestCase):
    def test_automatic_failure_is_not_marked_as_success(self):
        cfg = SimpleNamespace(settings=Settings())
        state = main.SyncState()
        manager = main.NtpSyncManager(cfg, state)
        selected = ntp_client.NtpResult("test.invalid", 100, 500, True)
        with patch.object(manager, "_choose_server", return_value=selected), \
             patch.object(time_utils, "set_system_time_utc_via_helper", return_value=(False, "权限不足")) as apply, \
             patch.object(main.time, "sleep", side_effect=lambda _: setattr(state, "stop_requested", True)):
            manager._run_loop()
        self.assertEqual(apply.call_args.kwargs, {"allow_elevation": False})
        self.assertEqual(state.last_success_time, 0)
        self.assertFalse(state.last_result.success)
        self.assertIn("权限不足", state.last_result.error)

    def test_small_drift_does_not_request_correction(self):
        manager = main.NtpSyncManager(SimpleNamespace(settings=Settings()), main.SyncState())
        with patch.object(time_utils, "set_system_time_utc_via_helper") as apply:
            self.assertFalse(manager._verify_and_apply_time(ntp_client.NtpResult("test.invalid", 10, 20, True))[0])
            apply.assert_not_called()

    def test_only_manual_sync_action_opts_into_elevation(self):
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        tray = main.TrayApp.__new__(main.TrayApp)
        tray.cfg = SimpleNamespace(settings=Settings())
        tray.state = main.SyncState()
        tray.sync_manager = main.NtpSyncManager(tray.cfg, tray.state)
        tray._on_status_changed = Mock()
        tray._notify = Mock()
        selected = ntp_client.NtpResult("test.invalid", 100, 500, True)
        with patch.object(tray.sync_manager, "_choose_server", return_value=selected), \
             patch.object(main.threading, "Thread", InlineThread), \
             patch.object(time_utils, "set_system_time_utc_via_helper", return_value=(False, "已取消")) as apply:
            tray.sync_now()
        self.assertEqual(apply.call_args.kwargs, {"allow_elevation": True})
        self.assertEqual(tray.state.last_success_time, 0)
        self.assertFalse(tray.state.last_result.success)


if __name__ == "__main__":
    unittest.main(verbosity=2)
