import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quota_watch.claude import (
    ClaudeError,
    _cached_reset_times,
    _write_snapshot,
    fetch_claude_snapshot,
    ingest_statusline,
    parse_oauth_usage,
)


class RememberedResetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cache = self.root / "claude.json"
        self.state = self.root / "refresh.json"
        self.history = self.root / "history.json"
        self.now = datetime.now(timezone.utc)
        self.reset = int(self.now.timestamp()) + 3600
        for attribute, target, kwargs in (
            ("native", "quota_watch.claude.query_oauth_usage_windows", {"side_effect": ClaudeError("offline")}),
            ("wsl", "quota_watch.claude.query_oauth_usage_wsl", {"side_effect": ClaudeError("offline")}),
            ("desktop", "quota_watch.claude._desktop_history_paths", {"return_value": [self.history]}),
            ("plan", "quota_watch.claude.query_subscription_type", {"return_value": None}),
        ):
            patcher = patch(target, **kwargs)
            self.addCleanup(patcher.stop)
            setattr(self, attribute, patcher.start())

    def remember(self, resets_at=None):
        snapshot = parse_oauth_usage({
            "five_hour": {"utilization": 70, "resets_at": self.reset if resets_at is None else resets_at},
            "subscription_type": "max",
            "limits": [{"kind": "weekly_scoped", "model_name": "Fable", "percent": 80,
                        "resets_at": self.reset + 3600}],
        })
        _write_snapshot(self.cache, replace(
            snapshot, captured_at=(self.now - timedelta(minutes=30)).isoformat(),
        ))

    def write_history(self, usage=None):
        self.history.write_text(json.dumps({"version": 2, "samples": [{
            "t": int(self.now.timestamp() * 1000), "org": "private-org", "u": usage or {"fh": 40, "sd": 42},
        }]}), encoding="utf-8")

    def fetch(self):
        return fetch_claude_snapshot(destination=self.cache, refresh_state=self.state)

    def test_desktop_reuses_only_reset_time_without_changing_usage_capture_or_cache(self):
        self.remember()
        before = self.cache.read_bytes()
        self.write_history()
        first = self.fetch()
        second = self.fetch()
        self.assertEqual([(w.used_percent, w.resets_at) for w in first.buckets[0].windows],
                         [(40, self.reset), (42, None)])
        expected_capture = datetime.fromtimestamp(int(self.now.timestamp() * 1000) / 1000, timezone.utc)
        self.assertEqual(first.captured_at, expected_capture.isoformat())
        self.assertEqual(second.buckets, first.buckets)
        self.assertEqual(len(first.buckets), 1)
        self.assertIsNone(first.buckets[0].plan_type)
        self.assertIn("Using remembered reset times", first.message)
        self.assertNotIn("remembered_reset_times", first.to_dict())
        self.assertEqual(self.cache.read_bytes(), before)
        self.native.assert_called_once()
        self.wsl.assert_not_called()

    def test_expired_reset_is_not_rolled_forward_or_attached_to_new_usage(self):
        self.remember(resets_at=int(self.now.timestamp()) - 60)
        self.write_history()
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 40)
        self.assertIsNone(result.buckets[0].windows[0].resets_at)
        self.assertNotIn("Using remembered reset times", result.message)

    def test_remembered_values_expire_and_are_pruned_from_the_next_cache_write(self):
        self.remember()
        with patch("quota_watch.claude.datetime", wraps=datetime) as clock:
            clock.now.return_value = self.now + timedelta(hours=3)
            self.assertEqual(_cached_reset_times(self.cache), {})
            result = _write_snapshot(self.cache, parse_oauth_usage({"five_hour": {"utilization": 1}}))
        self.assertIsNone(result.buckets[0].windows[0].resets_at)
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["remembered_reset_times"], [])

    def test_statusline_missing_reset_preserves_it_and_accepts_a_new_reset(self):
        self.remember()
        first = ingest_statusline({"rate_limits": {"five_hour": {"used_percentage": 41}}}, self.cache)
        self.assertEqual((first.buckets[0].windows[0].used_percent, first.buckets[0].windows[0].resets_at),
                         (41, self.reset))
        newer_reset = self.reset + 300
        second = ingest_statusline({"rate_limits": {"five_hour": {
            "used_percentage": 42, "resets_at": newer_reset,
        }}}, self.cache)
        self.assertEqual(second.buckets[0].windows[0].resets_at, newer_reset)
        third = ingest_statusline({"rate_limits": {"five_hour": {"used_percentage": 43}}}, self.cache)
        self.assertEqual(third.buckets[0].windows[0].resets_at, newer_reset)

    def test_native_query_missing_reset_uses_remembered_value_in_return_and_cache(self):
        self.remember()
        self.native.side_effect = None
        self.native.return_value = {"five_hour": {"utilization": 41}}
        first = self.fetch()
        second = self.fetch()
        for snapshot in (first, second):
            self.assertEqual((snapshot.buckets[0].windows[0].used_percent,
                              snapshot.buckets[0].windows[0].resets_at), (41, self.reset))
        self.wsl.assert_not_called()

    def test_wsl_query_preserves_model_and_all_model_resets_separately(self):
        self.remember()
        self.wsl.side_effect = None
        self.wsl.return_value = {"five_hour": {"utilization": 41}, "limits": [
            {"kind": "weekly_scoped", "model_name": "Fable", "percent": 81},
            {"kind": "weekly_scoped", "model_name": "Other", "percent": 1},
        ]}
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].resets_at, self.reset)
        self.assertEqual(result.buckets[1].windows[0].resets_at, self.reset + 3600)
        self.assertIsNone(result.buckets[2].windows[0].resets_at)
        self.assertEqual(result.buckets[1].windows[0].used_percent, 81)

    def test_reset_memory_survives_a_snapshot_that_omits_the_window(self):
        self.remember()
        self.native.side_effect = None
        self.native.return_value = {"seven_day": {"utilization": 42}}
        first = self.fetch()
        self.assertEqual([w.label for w in first.buckets[0].windows], ["7d"])
        self.assertEqual(_cached_reset_times(self.cache)[("claude", "5h", 300)], self.reset)
        payload = json.loads(self.cache.read_text(encoding="utf-8"))
        payload["captured_at"] = (self.now - timedelta(minutes=30)).isoformat()
        self.cache.write_text(json.dumps(payload), encoding="utf-8")
        self.write_history({"fh": 41})
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].label, "5h")
        self.assertEqual(result.buckets[0].windows[0].resets_at, self.reset)

    def test_invalid_or_mismatched_reset_memory_is_ignored_without_failure(self):
        valid = {"bucket_id": "claude", "label": "5h", "window_minutes": 300, "resets_at": self.reset}
        entries = [None, [], {}, {**valid, "resets_at": True}, {**valid, "resets_at": str(self.reset)},
                   {**valid, "window_minutes": []}, {**valid, "bucket_id": []},
                   {**valid, "bucket_id": "claude_model_fable"}, {**valid, "window_minutes": 60}]
        self.cache.write_text(json.dumps({"provider": "claude", "remembered_reset_times": entries}),
                              encoding="utf-8")
        self.write_history({"fh": 41})
        result = self.fetch()
        self.assertEqual(result.status, "available")
        self.assertIsNone(result.buckets[0].windows[0].resets_at)
        for payload in (None, [], {"provider": "codex", "remembered_reset_times": [valid]},
                        {"provider": "claude", "remembered_reset_times": {}, "buckets": [None, []]}):
            self.cache.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(_cached_reset_times(self.cache), {})
        self.cache.write_text("{", encoding="utf-8")
        self.assertEqual(_cached_reset_times(self.cache), {})

    def test_existing_cache_without_memory_field_remains_compatible(self):
        self.remember()
        payload = json.loads(self.cache.read_text(encoding="utf-8"))
        payload.pop("remembered_reset_times")
        self.cache.write_text(json.dumps(payload), encoding="utf-8")
        self.write_history()
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].resets_at, self.reset)


if __name__ == "__main__":
    unittest.main()
