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
    configure_statusline,
    ingest_statusline,
    query_subscription_type,
    read_cached_snapshot,
    statusline_command,
)
from quota_watch.cli import run_claude_ingest
from quota_watch.models import unavailable_snapshot


class ClaudeBridgeTests(unittest.TestCase):
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
