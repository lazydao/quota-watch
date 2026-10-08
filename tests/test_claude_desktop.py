import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quota_watch.claude import (
    ClaudeError,
    _desktop_history_paths,
    _write_snapshot,
    fetch_claude_snapshot,
    read_desktop_snapshot,
)
from quota_watch.models import ProviderSnapshot, QuotaBucket, QuotaWindow


def sample(captured: datetime, usage: dict) -> dict:
    return {"t": int(captured.timestamp() * 1000), "org": "private-org", "u": usage}


def complete_snapshot(captured: datetime) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider="claude",
        status="available",
        captured_at=captured.isoformat(),
        buckets=[
            QuotaBucket(
                bucket_id="claude", name="All models", plan_type="max",
                windows=[QuotaWindow("5h", 20, 300, 2_000_000_000)],
            ),
            QuotaBucket(
                bucket_id="claude_model_fable", name="Fable", plan_type="max",
                windows=[QuotaWindow("7d", 30, 10_080, 2_000_000_000)],
            ),
        ],
    )


class ClaudeDesktopTests(unittest.TestCase):
    def test_discovers_windows_store_and_regular_install_histories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roaming = Path(directory) / "Roaming"
            local = Path(directory) / "Local"
            environment = SimpleNamespace(
                name="nt", environ={"APPDATA": str(roaming), "LOCALAPPDATA": str(local)},
            )
            with patch("quota_watch.claude.os", environment):
                paths = _desktop_history_paths()
            self.assertEqual(paths, [
                roaming / "Claude" / "plan-usage-history.json",
                local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache"
                / "Roaming" / "Claude" / "plan-usage-history.json",
            ])

    def test_automatic_discovery_is_windows_only(self) -> None:
        with patch("quota_watch.claude.os", SimpleNamespace(name="posix", environ={})):
            self.assertEqual(_desktop_history_paths(), [])

    def test_latest_sample_is_filtered_without_inventing_missing_fields(self) -> None:
        now = datetime.now(timezone.utc)
        newest = sample(now - timedelta(minutes=2), {"fh": 0, "sd": 4, "xu": 90})
        payload = {
            "version": 2,
            "samples": [newest, sample(now - timedelta(minutes=4), {"fh": 9, "sd": 3})],
            "subscription_type": "must-not-be-retained",
            "limits": [{"model_name": "must-not-be-retained", "percent": 65}],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            source.write_text(json.dumps(payload), encoding="utf-8-sig")
            snapshot = read_desktop_snapshot(source)
        self.assertEqual(snapshot.status, "available")
        self.assertEqual(
            [(w.label, w.used_percent, w.window_minutes) for w in snapshot.buckets[0].windows],
            [("5h", 0, 300), ("7d", 4, 10_080)],
        )
        self.assertEqual(
            snapshot.captured_at,
            datetime.fromtimestamp(newest["t"] / 1000, timezone.utc).isoformat(),
        )
        self.assertTrue(all(w.resets_at is None for w in snapshot.buckets[0].windows))
        self.assertIsNone(snapshot.buckets[0].plan_type)
        self.assertEqual(len(snapshot.buckets), 1)
        serialized = json.dumps(snapshot.to_dict())
        self.assertNotIn("private-org", serialized)
        self.assertNotIn("must-not-be-retained", serialized)
        self.assertNotIn('"xu"', serialized)
        self.assertIn("Claude Desktop fallback", snapshot.message)

    def test_missing_window_is_not_shown_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            source.write_text(json.dumps({
                "version": 2, "samples": [sample(datetime.now(timezone.utc), {"sd": 4})],
            }), encoding="utf-8")
            snapshot = read_desktop_snapshot(source)
        self.assertEqual([w.label for w in snapshot.buckets[0].windows], ["7d"])

    def test_old_sample_is_stale_even_when_file_was_just_written(self) -> None:
        old = sample(datetime.now(timezone.utc) - timedelta(minutes=28), {"fh": 9, "sd": 4})
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            source.write_text(json.dumps({"version": 2, "samples": [old]}), encoding="utf-8")
            snapshot = read_desktop_snapshot(source)
        self.assertEqual(snapshot.status, "stale")
        self.assertIn("28 min old", snapshot.message)
        self.assertEqual(
            snapshot.captured_at,
            datetime.fromtimestamp(old["t"] / 1000, timezone.utc).isoformat(),
        )

    def test_chooses_newest_history_across_installs(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / "regular.json", Path(directory) / "store.json"]
            for path, age, used in zip(paths, (10, 2), (5, 9)):
                path.write_text(json.dumps({
                    "version": 2, "samples": [sample(now - timedelta(minutes=age), {"fh": used})],
                }), encoding="utf-8")
            with patch("quota_watch.claude._desktop_history_paths", return_value=paths):
                snapshot = read_desktop_snapshot()
        self.assertEqual(snapshot.buckets[0].windows[0].used_percent, 9)

    def test_invalid_histories_are_unavailable_without_exceptions(self) -> None:
        now = datetime.now(timezone.utc)
        invalid_samples = [
            None, {}, {"t": True, "u": {"fh": 9}}, {"t": "invalid", "u": {"fh": 9}},
            {"t": float("nan"), "u": {"fh": 9}}, {"t": float("inf"), "u": {"fh": 9}},
            {"t": 0, "u": {"fh": 9}}, {"t": -1, "u": {"fh": 9}},
            sample(now + timedelta(days=1), {"fh": 9}),
            sample(now, {"fh": None, "sd": float("nan")}),
            sample(now, {"fh": True, "sd": -1}),
            sample(now, {"fh": 10**400}),
            {"t": int(now.timestamp() * 1000), "u": []},
        ]
        payloads = [None, [], {}, {"version": 1, "samples": []},
                    {"version": 2, "samples": {}}, {"version": 2, "samples": invalid_samples}]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            for payload in payloads:
                with self.subTest(payload=payload):
                    source.write_text(json.dumps(payload), encoding="utf-8")
                    self.assertEqual(read_desktop_snapshot(source).status, "unavailable")
            source.write_text("{", encoding="utf-8")
            self.assertEqual(read_desktop_snapshot(source).status, "unavailable")
            source.write_bytes(b"\xff")
            self.assertEqual(read_desktop_snapshot(source).status, "unavailable")
            self.assertEqual(read_desktop_snapshot(Path(directory) / "missing.json").status, "unavailable")
            with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
                self.assertEqual(read_desktop_snapshot(source).status, "unavailable")
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=11 * 1024 * 1024)):
                self.assertEqual(read_desktop_snapshot(source).status, "unavailable")

    def test_bad_samples_do_not_hide_a_valid_sample(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            source.write_text(json.dumps({"version": 2, "samples": [
                sample(now - timedelta(minutes=1), {"fh": 100.5}),
                sample(now, {"fh": None}), {"t": 10**400, "u": {"fh": 90}},
            ]}), encoding="utf-8")
            snapshot = read_desktop_snapshot(source)
        self.assertEqual(snapshot.buckets[0].windows[0].used_percent, 100)

    def test_invalid_percentage_does_not_hide_other_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            source.write_text(json.dumps({"version": 2, "samples": [
                sample(datetime.now(timezone.utc), {"fh": 10**400, "sd": 4}),
            ]}), encoding="utf-8")
            snapshot = read_desktop_snapshot(source)
        self.assertEqual([w.label for w in snapshot.buckets[0].windows], ["7d"])

    def test_failed_refresh_uses_desktop_without_overwriting_complete_cache(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            state = Path(directory) / "refresh.json"
            history = Path(directory) / "history.json"
            _write_snapshot(destination, complete_snapshot(now - timedelta(minutes=20)))
            original = destination.read_text(encoding="utf-8")
            history.write_text(json.dumps({
                "version": 2, "samples": [sample(now - timedelta(minutes=2), {"fh": 9, "sd": 4})],
            }), encoding="utf-8")
            with patch("quota_watch.claude._desktop_history_paths", return_value=[history]):
                with patch("quota_watch.claude.query_oauth_usage_wsl", side_effect=ClaudeError("offline")) as query:
                    first = fetch_claude_snapshot(destination=destination, refresh_state=state)
                    history.write_text(json.dumps({
                        "version": 2, "samples": [sample(now - timedelta(minutes=1), {"fh": 10, "sd": 4})],
                    }), encoding="utf-8")
                    second = fetch_claude_snapshot(destination=destination, refresh_state=state)
            self.assertEqual(destination.read_text(encoding="utf-8"), original)
            self.assertEqual(query.call_count, 1)
        self.assertEqual(first.buckets[0].windows[0].used_percent, 9)
        self.assertEqual(second.buckets[0].windows[0].used_percent, 10)
        self.assertIsNone(first.buckets[0].plan_type)
        self.assertIsNone(first.buckets[0].windows[0].resets_at)
        self.assertEqual(len(first.buckets), 1)

    def test_recent_complete_cache_stays_preferred_after_query_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            _write_snapshot(destination, complete_snapshot(datetime.now(timezone.utc) - timedelta(minutes=2)))
            with patch("quota_watch.claude.read_desktop_snapshot") as desktop:
                with patch("quota_watch.claude.query_oauth_usage_wsl", side_effect=ClaudeError("offline")):
                    snapshot = fetch_claude_snapshot(
                        destination=destination, refresh_state=Path(directory) / "refresh.json",
                    )
            desktop.assert_not_called()
        self.assertEqual([b.name for b in snapshot.buckets], ["All models", "Fable"])
        self.assertEqual(snapshot.buckets[0].plan_type, "max")

    def test_older_desktop_history_does_not_replace_stale_cli_cache(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            history = Path(directory) / "history.json"
            _write_snapshot(destination, complete_snapshot(now - timedelta(minutes=20)))
            history.write_text(json.dumps({
                "version": 2, "samples": [sample(now - timedelta(minutes=30), {"fh": 9})],
            }), encoding="utf-8")
            with patch("quota_watch.claude._desktop_history_paths", return_value=[history]):
                with patch("quota_watch.claude.query_oauth_usage_wsl", side_effect=ClaudeError("offline")):
                    snapshot = fetch_claude_snapshot(
                        destination=destination, refresh_state=Path(directory) / "refresh.json",
                    )
        self.assertEqual(snapshot.status, "stale")
        self.assertEqual(snapshot.buckets[0].windows[0].used_percent, 20)
        self.assertEqual(snapshot.buckets[1].name, "Fable")

    def test_missing_cache_can_use_desktop_and_recover_to_active_refresh(self) -> None:
        now = datetime.now(timezone.utc)
        payload = {"subscription_type": "max", "limits": [
            {"kind": "session", "percent": 10, "resets_at": "2033-05-18T03:33:20Z"},
            {"kind": "weekly_scoped", "percent": 30, "model_name": "Fable"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            history = Path(directory) / "history.json"
            history.write_text(json.dumps({
                "version": 2, "samples": [sample(now - timedelta(minutes=2), {"fh": 9})],
            }), encoding="utf-8")
            with patch("quota_watch.claude._desktop_history_paths", return_value=[history]):
                with patch("quota_watch.claude.query_oauth_usage_wsl", side_effect=[ClaudeError("offline"), payload]):
                    first = fetch_claude_snapshot(
                        destination=destination, refresh_state=Path(directory) / "refresh.json",
                        refresh_interval_seconds=0,
                    )
                    self.assertFalse(destination.exists())
                    second = fetch_claude_snapshot(
                        destination=destination, refresh_state=Path(directory) / "refresh.json",
                        refresh_interval_seconds=0,
                    )
                    self.assertTrue(destination.exists())
        self.assertIn("Claude Desktop fallback", first.message)
        self.assertEqual([b.name for b in second.buckets], ["All models", "Fable"])
        self.assertEqual(second.buckets[0].plan_type, "max")
        self.assertIsNotNone(second.buckets[0].windows[0].resets_at)
        self.assertIsNone(second.message)

    def test_missing_or_broken_desktop_history_keeps_unavailable_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history.json"
            history.write_text("{", encoding="utf-8")
            with patch("quota_watch.claude._desktop_history_paths", return_value=[history]):
                with patch("quota_watch.claude.query_oauth_usage_wsl", side_effect=ClaudeError("offline")):
                    snapshot = fetch_claude_snapshot(
                        destination=Path(directory) / "claude.json",
                        refresh_state=Path(directory) / "refresh.json",
                    )
        self.assertEqual(snapshot.status, "unavailable")
        self.assertIn("offline", snapshot.message)


if __name__ == "__main__":
    unittest.main()
