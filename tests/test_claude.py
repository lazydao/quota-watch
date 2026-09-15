import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from quota_watch.claude import (
    ClaudeError,
    configure_statusline,
    fetch_claude_snapshot,
    ingest_statusline,
    parse_oauth_usage,
    query_oauth_usage_wsl,
    query_subscription_type,
    read_cached_snapshot,
    statusline_command,
)
from quota_watch.cli import run_claude_ingest
from quota_watch.models import unavailable_snapshot


class ClaudeBridgeTests(unittest.TestCase):
    def test_model_limits_survive_cache_and_statusline(self) -> None:
        payload = {"subscription_type": "max", "limits": [
            {"kind": "weekly_all", "percent": 57, "resets_at": "2027-01-20T00:00:00Z"},
            {"kind": "weekly_scoped", "percent": 65, "model_name": "Fable", "resets_at": "2027-01-20T00:00:00Z"},
            {"kind": "weekly_scoped", "percent": None, "model_name": "Missing"},
            None,
        ]}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            with patch("quota_watch.claude.query_oauth_usage_wsl", return_value=payload):
                snapshot = fetch_claude_snapshot(destination=destination, refresh_state=Path(directory) / "refresh.json")
            self.assertEqual([b.name for b in snapshot.buckets], ["All models", "Fable"])
            cached = read_cached_snapshot(destination)
            self.assertEqual(cached.buckets[1].windows[0].used_percent, 65)
            ingest_statusline({"rate_limits": {"five_hour": {"used_percentage": 4, "resets_at": 1800016200}}}, destination)
            cached = read_cached_snapshot(destination)
            self.assertEqual(cached.buckets[1].name, "Fable")
            self.assertEqual(cached.buckets[1].windows[0].used_percent, 65)

    def test_missing_model_limits_do_not_create_zero_bucket(self) -> None:
        for limits in (None, {}, [], [None, {"kind": "weekly_scoped", "model_name": "Fable"}]):
            snapshot = parse_oauth_usage({"seven_day": {"utilization": 57}, "limits": limits})
            self.assertEqual(len(snapshot.buckets), 1)

    def test_oauth_usage_parses_filtered_windows(self) -> None:
        snapshot = parse_oauth_usage(
            {
                "five_hour": {
                    "utilization": 23.5,
                    "resets_at": "2027-01-15T12:30:00+00:00",
                },
                "seven_day": {
                    "utilization": 41,
                    "resets_at": "2027-01-20T00:00:00Z",
                },
                "subscription_type": "pro",
                "account": "must-not-be-retained",
            }
        )

        self.assertEqual(snapshot.status, "available")
        self.assertEqual(snapshot.buckets[0].plan_type, "pro")
        self.assertEqual(
            [(window.label, window.used_percent) for window in snapshot.buckets[0].windows],
            [("5h", 23.5), ("7d", 41.0)],
        )
        self.assertEqual(snapshot.buckets[0].windows[0].resets_at, 1_800_016_200)

    def test_oauth_query_keeps_credentials_inside_wsl(self) -> None:
        payload = {
            "five_hour": {"utilization": 12, "resets_at": "2027-01-15T12:30:00Z"},
            "subscription_type": "pro",
        }
        completed = CompletedProcess([], 0, json.dumps(payload), "")

        with patch("quota_watch.claude.os.name", "nt"):
            with patch("quota_watch.claude.shutil.which", return_value="wsl.exe"):
                with patch("quota_watch.claude.subprocess.run", return_value=completed) as run:
                    result = query_oauth_usage_wsl(timeout=4, distro="Debian")

        self.assertEqual(result, payload)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["wsl.exe", "--distribution", "Debian"])
        self.assertIn("python3", command)
        self.assertNotIn("private-oauth-token", " ".join(command))

    def test_active_refresh_is_throttled_for_ten_minutes(self) -> None:
        payload = {
            "five_hour": {"utilization": 25, "resets_at": "2027-01-15T12:30:00Z"},
            "seven_day": {"utilization": 40, "resets_at": "2027-01-20T00:00:00Z"},
            "subscription_type": "pro",
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            refresh_state = Path(directory) / "claude-oauth-refresh.json"
            with patch("quota_watch.claude.query_oauth_usage_wsl", return_value=payload) as query:
                first = fetch_claude_snapshot(destination=destination, refresh_state=refresh_state)
                second = fetch_claude_snapshot(destination=destination, refresh_state=refresh_state)

        self.assertEqual(query.call_count, 1)
        self.assertEqual(first.buckets[0].windows[0].used_percent, 25)
        self.assertEqual(second.buckets[0].windows[1].used_percent, 40)

    def test_failed_active_refresh_preserves_cache_and_is_throttled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            refresh_state = Path(directory) / "claude-oauth-refresh.json"
            ingest_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 35, "resets_at": 2_000_000_000},
                    }
                },
                destination,
            )
            with patch("quota_watch.claude.query_subscription_type", return_value=None):
                with patch(
                    "quota_watch.claude.query_oauth_usage_wsl",
                    side_effect=ClaudeError("offline"),
                ) as query:
                    first = fetch_claude_snapshot(destination=destination, refresh_state=refresh_state)
                    second = fetch_claude_snapshot(destination=destination, refresh_state=refresh_state)

        self.assertEqual(query.call_count, 1)
        self.assertEqual(first.buckets[0].windows[0].used_percent, 35)
        self.assertEqual(second.buckets[0].windows[0].used_percent, 35)

    def test_read_cache_hides_windows_after_their_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            ingest_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 90, "resets_at": 1},
                    }
                },
                destination,
            )
            with patch("quota_watch.claude.query_subscription_type", return_value=None):
                snapshot = read_cached_snapshot(destination, stale_after_seconds=10**9)

        self.assertEqual(snapshot.buckets[0].windows, [])

    def test_missing_rate_limits_waits_without_error(self) -> None:
        with patch.object(sys, "stdin", io.StringIO('{"model":{"display_name":"Claude"}}')):
            with patch("quota_watch.cli.read_cached_snapshot", return_value=unavailable_snapshot("claude", "empty")):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = run_claude_ingest()

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), "Claude quota waiting for first response")

    def test_cache_contains_only_filtered_quota_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            ingest_statusline(
                {
                    "session_id": "secret-session-id",
                    "cwd": "C:/private/project",
                    "rate_limits": {
                        "five_hour": {"used_percentage": 12.5, "resets_at": 1_800_000_000},
                        "seven_day": {"used_percentage": 42, "resets_at": 1_800_100_000},
                    },
                },
                destination,
            )
            content = destination.read_text(encoding="utf-8")
            self.assertNotIn("secret-session-id", content)
            self.assertNotIn("private/project", content)

            with patch("quota_watch.claude.query_subscription_type", return_value=None):
                snapshot = read_cached_snapshot(destination, stale_after_seconds=10**9)
            self.assertEqual(snapshot.status, "available")
            self.assertEqual(snapshot.buckets[0].windows[0].used_percent, 12.5)

    def test_partial_rate_limits_preserve_unexpired_cached_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            ingest_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 100, "resets_at": 2_000_000_000},
                        "seven_day": {"used_percentage": 24, "resets_at": 2_000_100_000},
                    },
                },
                destination,
            )

            ingest_statusline(
                {
                    "rate_limits": {
                        "seven_day": {"used_percentage": 25, "resets_at": 2_000_100_000},
                    },
                },
                destination,
            )

            with patch("quota_watch.claude.query_subscription_type", return_value=None):
                snapshot = read_cached_snapshot(destination, stale_after_seconds=10**9)
            windows = snapshot.buckets[0].windows
            self.assertEqual([window.label for window in windows], ["5h", "7d"])
            self.assertEqual(windows[0].used_percent, 100)
            self.assertEqual(windows[1].used_percent, 25)

    def test_partial_rate_limits_drop_expired_cached_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            ingest_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 100, "resets_at": 1},
                        "seven_day": {"used_percentage": 24, "resets_at": 2_000_100_000},
                    },
                },
                destination,
            )

            ingest_statusline(
                {
                    "rate_limits": {
                        "seven_day": {"used_percentage": 25, "resets_at": 2_000_100_000},
                    },
                },
                destination,
            )

            with patch("quota_watch.claude.query_subscription_type", return_value=None):
                snapshot = read_cached_snapshot(destination, stale_after_seconds=10**9)
            self.assertEqual([window.label for window in snapshot.buckets[0].windows], ["7d"])

    def test_exhausted_percentage_replaces_cached_value_during_extra_usage(self) -> None:
        for exhausted_value in (100.5, "100"):
            with self.subTest(exhausted_value=exhausted_value):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "claude.json"
                    ingest_statusline(
                        {
                            "rate_limits": {
                                "five_hour": {"used_percentage": 80, "resets_at": 2_000_000_000},
                                "seven_day": {"used_percentage": 12, "resets_at": 2_000_100_000},
                            },
                        },
                        destination,
                    )

                    ingest_statusline(
                        {
                            "rate_limits": {
                                "five_hour": {
                                    "used_percentage": exhausted_value,
                                    "resets_at": 2_000_000_000,
                                },
                                "seven_day": {"used_percentage": 13, "resets_at": 2_000_100_000},
                            },
                        },
                        destination,
                    )

                    with patch("quota_watch.claude.query_subscription_type", return_value=None):
                        snapshot = read_cached_snapshot(destination, stale_after_seconds=10**9)
                    windows = snapshot.buckets[0].windows
                    self.assertEqual(windows[0].used_percent, 100)
                    self.assertEqual(windows[1].used_percent, 13)

    def test_read_cache_adds_only_claude_subscription_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "claude.json"
            ingest_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 12.5},
                    },
                },
                destination,
            )
            auth_status = {
                "loggedIn": True,
                "subscriptionType": "pro",
                "email": "private@example.com",
                "orgName": "Private Org",
            }
            completed = CompletedProcess([], 0, json.dumps(auth_status), "")
            with patch("quota_watch.claude.shutil.which", return_value="claude.exe"):
                with patch("quota_watch.claude.subprocess.run", return_value=completed):
                    snapshot = read_cached_snapshot(destination, stale_after_seconds=10**9)

            self.assertEqual(snapshot.buckets[0].plan_type, "pro")
            content = destination.read_text(encoding="utf-8")
            self.assertIn('"plan_type": "pro"', content)
            self.assertNotIn("private@example.com", content)
            self.assertNotIn("Private Org", content)

            ingest_statusline(
                {"rate_limits": {"five_hour": {"used_percentage": 20}}},
                destination,
            )
            preserved = read_cached_snapshot(destination, stale_after_seconds=10**9)
            self.assertEqual(preserved.buckets[0].plan_type, "pro")

    def test_subscription_type_falls_back_to_wsl_on_windows(self) -> None:
        local = CompletedProcess([], 1, "", "not logged in")
        wsl = CompletedProcess([], 0, json.dumps({"loggedIn": True, "subscriptionType": "max"}), "")

        def find_executable(name: str) -> str | None:
            return {"claude": "claude.exe", "wsl.exe": "wsl.exe"}.get(name)

        with patch("quota_watch.claude.os.name", "nt"):
            with patch("quota_watch.claude.shutil.which", side_effect=find_executable):
                with patch("quota_watch.claude.subprocess.run", side_effect=[local, wsl]) as run:
                    self.assertEqual(query_subscription_type(), "max")

        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][:4], ["wsl.exe", "--exec", "bash", "-lc"])

    def test_setup_does_not_replace_existing_statusline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(json.dumps({"statusLine": {"command": "existing"}}), encoding="utf-8")
            changed, _, _ = configure_statusline(settings)
            self.assertFalse(changed)
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["statusLine"]["command"], "existing")

    def test_setup_preserves_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
            changed, _, entry = configure_statusline(settings)
            self.assertTrue(changed)
            payload = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(payload["theme"], "dark")
            self.assertEqual(payload["statusLine"], entry)
            self.assertTrue(settings.with_name("settings.json.quota-watch.bak").exists())

    def test_windows_statusline_uses_cross_shell_launcher(self) -> None:
        command = statusline_command()
        if os.name == "nt":
            self.assertTrue(command.startswith("powershell.exe "))
        self.assertIn("quota_watch claude-ingest", command)


if __name__ == "__main__":
    unittest.main()
