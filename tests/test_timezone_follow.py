import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from PySide6.QtCore import QCoreApplication, QDateTime, QTimeZone, Qt

from app.config import Settings
from app import time_utils
from app.timezone_follow import GeoResolver, GeoResult, TimezoneFollower, choose_consensus


IP = "8.8.8.8"  # Synthetic fixture; no test sends real network requests.
LA = GeoResult(IP, "US", "America/Los_Angeles", "Pacific Standard Time", ("a", "b"), ())


def vote(source, iana="America/Los_Angeles", country="US", ip=IP):
    return {"source": source, "iana": iana, "country": country, "ip": ip}


class MappingTests(unittest.TestCase):
    def test_regions_and_non_whole_hour_zones(self):
        expected = {
            "America/Los_Angeles": "Pacific Standard Time", "US/Pacific": "Pacific Standard Time",
            "Asia/Shanghai": "China Standard Time", "Asia/Tokyo": "Tokyo Standard Time",
            "Asia/Kolkata": "India Standard Time", "Asia/Kathmandu": "Nepal Standard Time",
            "Australia/Lord_Howe": "Lord Howe Standard Time", "Europe/London": "GMT Standard Time",
            "Etc/UTC": "UTC",
        }
        for iana, windows in expected.items():
            with self.subTest(iana=iana):
                self.assertEqual(time_utils.iana_to_windows(iana), windows)

    def test_daylight_saving_rules_are_preserved(self):
        zone = QTimeZone(b"America/Los_Angeles")
        winter = QDateTime.fromString("2026-01-15T12:00:00Z", Qt.ISODate)
        summer = QDateTime.fromString("2026-07-15T12:00:00Z", Qt.ISODate)
        self.assertEqual(zone.offsetFromUtc(winter), -8 * 3600)
        self.assertEqual(zone.offsetFromUtc(summer), -7 * 3600)

    def test_unknown_zone_never_falls_back_to_an_offset(self):
        with self.assertRaises(ValueError):
            time_utils.iana_to_windows("Unknown/Invalid")

    def test_success_requires_readback(self):
        with patch.object(time_utils.subprocess, "check_call"), patch.object(time_utils, "get_current_timezone", return_value="China Standard Time"):
            self.assertFalse(time_utils.set_windows_timezone("Pacific Standard Time")[0])


class ConsensusTests(unittest.TestCase):
    def test_country_mismatch_is_excluded(self):
        result = choose_consensus(IP, "US", [vote("a"), vote("b"), vote("c", "Asia/Dhaka", "BD")])
        self.assertEqual(result.windows, LA.windows)
        self.assertEqual(set(result.sources), {"a", "b"})

    def test_majority_and_single_disagreement(self):
        result = choose_consensus(IP, "US", [vote("a"), vote("b"), vote("c", "America/Chicago")])
        self.assertEqual(result.windows, LA.windows)

    def test_single_source_and_ties_are_blocked(self):
        for votes in [[vote("a")], [vote("a"), vote("b", "America/Chicago")],
                      [vote("a"), vote("b"), vote("c", "America/Chicago"), vote("d", "America/Chicago")]]:
            with self.subTest(votes=votes), self.assertRaises(ValueError):
                choose_consensus(IP, "US", votes)

    def test_mismatched_ip_and_duplicate_source_do_not_count(self):
        for votes in [[vote("a"), vote("b", ip="1.1.1.1")], [vote("a"), vote("a")]]:
            with self.subTest(votes=votes), self.assertRaises(ValueError):
                choose_consensus(IP, "US", votes)

    def test_aliases_are_compared_by_windows_zone(self):
        self.assertEqual(choose_consensus(IP, "US", [vote("a"), vote("b", "US/Pacific")]).windows, LA.windows)


class FakeHTTP:
    def __init__(self, changed_exit=False, fail_sources=False):
        self.calls = []
        self.changed_exit = changed_exit
        self.fail_sources = fail_sources
        self.probes = 0

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        response = Mock()
        if "cdn-cgi/trace" in url:
            self.probes += 1
            ip = "1.1.1.1" if self.changed_exit and self.probes > 1 else IP
            response.text = f"ip={ip}\nloc=US\n"
        else:
            if self.fail_sources:
                raise requests.Timeout()
            response.json.return_value = {"ip": IP, "country_code": "US", "timezone": "America/Los_Angeles"}
        return response


class ResolverTests(unittest.TestCase):
    def test_lookup_queries_explicit_ip_and_reuses_success_cache(self):
        http = FakeHTTP()
        resolver = GeoResolver(get=http, clock=lambda: 1)
        self.assertEqual(resolver.lookup().windows, LA.windows)
        self.assertEqual(resolver.lookup().windows, LA.windows)
        geo_calls = [url for url in http.calls if "trace" not in url]
        self.assertEqual(len(geo_calls), 3)
        self.assertTrue(all(IP in url for url in geo_calls))
        self.assertEqual(http.probes, 4)

    def test_exit_change_during_lookup_blocks_application(self):
        resolver = GeoResolver(get=FakeHTTP(changed_exit=True))
        with self.assertRaisesRegex(ValueError, "出口发生变化"):
            resolver.lookup()

    def test_failure_cooldown_prevents_repeated_geo_requests(self):
        http = FakeHTTP(fail_sources=True)
        resolver = GeoResolver(get=http, clock=lambda: 1)
        for _ in range(2):
            with self.assertRaises(ValueError):
                resolver.lookup()
        self.assertEqual(len([url for url in http.calls if "trace" not in url]), 3)

    def test_cache_expiry_refreshes_geolocation(self):
        http = FakeHTTP()
        clock = Mock(return_value=1)
        resolver = GeoResolver(get=http, clock=clock)
        resolver.lookup()
        clock.return_value = 21602
        resolver.lookup()
        self.assertEqual(len([url for url in http.calls if "trace" not in url]), 6)


class FollowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.profile = tempfile.TemporaryDirectory(prefix="timeguardian-tests-")

    @classmethod
    def tearDownClass(cls):
        logger = logging.getLogger("TimeGuardian.timezone")
        for handler in list(logger.handlers):
            if getattr(handler, "baseFilename", "").startswith(cls.profile.name):
                handler.close()
                logger.removeHandler(handler)
        cls.profile.cleanup()

    def setUp(self):
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": self.profile.name})
        self.environment.start()
        self.cfg = SimpleNamespace(settings=Settings(auto_timezone_enabled=True))
        self.follower = TimezoneFollower(self.cfg, resolver=SimpleNamespace(lookup=lambda **kwargs: LA))
        self.follower._enabled = True
        self.current = patch.object(time_utils, "get_current_timezone", return_value="China Standard Time")
        self.current_mock = self.current.start()
        self.setter = patch.object(time_utils, "set_windows_timezone", return_value=(True, "已设置测试时区"))
        self.setter_mock = self.setter.start()

    def tearDown(self):
        self.follower.stop()
        self.current.stop()
        self.setter.stop()
        self.environment.stop()

    def finish(self, result=LA, error="", manual=False, generation=0):
        self.follower._finish(generation, manual, result, error)

    def test_two_stable_checks_are_required(self):
        self.finish()
        self.setter_mock.assert_not_called()
        self.assertIn("1/2", self.follower.status)
        self.finish()
        self.setter_mock.assert_called_once_with(LA.windows)

    def test_node_flapping_resets_confirmation(self):
        other = GeoResult("1.1.1.1", "US", "America/Chicago", "Central Standard Time", ("a", "b"), ())
        self.finish(); self.finish(other); self.finish()
        self.setter_mock.assert_not_called()

    def test_error_breaks_consecutive_confirmation(self):
        self.finish(); self.finish(None, "网络不可用"); self.finish()
        self.setter_mock.assert_not_called()
        self.assertIn("1/2", self.follower.status)

    def test_disabling_rejects_an_inflight_result(self):
        self.cfg.settings.auto_timezone_enabled = False
        self.follower.configure()
        self.finish(manual=True)
        self.setter_mock.assert_not_called()

    def test_manual_check_can_apply_a_consensus_result_immediately(self):
        self.finish(manual=True)
        self.setter_mock.assert_called_once_with(LA.windows)

    def test_already_matching_does_not_write_or_notify(self):
        self.current_mock.return_value = LA.windows
        notices = []
        self.follower.notice.connect(lambda *args: notices.append(args))
        self.finish(); self.finish()
        self.setter_mock.assert_not_called()
        self.assertEqual(notices, [])

    def test_worker_result_is_delivered_through_event_loop(self):
        self.follower.request_check(manual=True)
        deadline = time.monotonic() + 2
        while self.follower._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.follower._busy)
        self.setter_mock.assert_called_once_with(LA.windows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
