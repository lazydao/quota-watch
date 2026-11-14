import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quota_watch import cli
from quota_watch.gemini import (
    GeminiError,
    compact_status,
    configure_statusline,
    fetch_gemini_snapshot,
    ingest_statusline,
    parse_statusline,
    read_cached_snapshot,
)
from quota_watch.wsl import configure_wsl_gemini_files


NOW = 1_800_000_000


def _payload() -> dict:
    return {
        "product": "antigravity",
        "plan_tier": "Pro",
        "email": "person@example.com",
        "quota": {
            "3p-weekly": {
                "remaining_fraction": 0.5,
                "reset_time": "2099-01-08T00:00:00Z",
                "reset_in_seconds": 600,
            },
            "gemini-weekly": {
                "remaining_fraction": 0.75,
                "reset_time": "2099-01-08T00:00:00Z",
                "reset_in_seconds": 600,
            },
            "gemini-5h": {"remaining_fraction": 0.9, "reset_in_seconds": 3600},
        },
    }


class ParseTests(unittest.TestCase):
    def test_groups_buckets_and_converts_remaining_to_used(self) -> None:
        snapshot = parse_statusline(_payload(), now=NOW)

        self.assertEqual(snapshot.provider, "gemini")
        self.assertEqual([bucket.name for bucket in snapshot.buckets], ["Gemini", "Claude + GPT"])
        gemini = snapshot.buckets[0]
        self.assertEqual(gemini.bucket_id, "gemini_gemini")
        self.assertEqual(gemini.plan_type, "Pro")
        self.assertEqual([window.label for window in gemini.windows], ["5h", "7d"])
        self.assertEqual([window.window_minutes for window in gemini.windows], [300, 10_080])
        self.assertAlmostEqual(gemini.windows[0].used_percent, 10.0)
        self.assertEqual(gemini.windows[0].resets_at, NOW + 3600)
        self.assertAlmostEqual(gemini.windows[1].used_percent, 25.0)
        expected = int(datetime(2099, 1, 8, tzinfo=timezone.utc).timestamp())
        self.assertEqual(gemini.windows[1].resets_at, expected)

    def test_skips_disabled_amount_only_and_malformed_buckets(self) -> None:
        snapshot = parse_statusline(
            {
                "quota": {
                    "gemini-weekly": {"remaining_fraction": 1.4},
                    "gemini-5h": {"remaining_fraction": 0.2, "disabled": True},
                    "credits": {"remaining_amount": 42},
                    "3p-weekly": {"remaining_fraction": "0.5"},
                    "broken": None,
                    "flash": {"remaining_fraction": 0.4},
                }
            },
            now=NOW,
        )

        self.assertEqual([bucket.name for bucket in snapshot.buckets], ["Gemini", "flash"])
        self.assertEqual(snapshot.buckets[0].windows[0].used_percent, 0.0)
        self.assertEqual(snapshot.buckets[1].windows[0].label, "quota")
        self.assertIsNone(snapshot.buckets[1].windows[0].window_minutes)
        self.assertIsNone(snapshot.buckets[1].windows[0].resets_at)

    def test_rejects_payload_without_usable_quota(self) -> None:
        for payload in ({}, {"quota": []}, {"quota": {}}, {"quota": {"gemini-5h": {}}}):
            with self.assertRaises(GeminiError):
                parse_statusline(payload, now=NOW)


class CacheTests(unittest.TestCase):
    def test_ingest_stores_only_filtered_quota(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "gemini.json"
            snapshot = ingest_statusline(_payload(), destination)
            raw = destination.read_text(encoding="utf-8")

            self.assertNotIn("person@example.com", raw)
            self.assertNotIn("email", raw)
            cached = read_cached_snapshot(destination)
            self.assertEqual(cached.status, "available")
            self.assertIsNone(cached.message)
            self.assertEqual(cached.buckets, snapshot.buckets)
            self.assertEqual(compact_status(cached), "Gemini 5h 10% · 7d 25% | Claude + GPT 7d 50%")

    def test_provider_is_hidden_until_bridge_delivers_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "gemini.json"
            self.assertIsNone(fetch_gemini_snapshot(destination))
            ingest_statusline(_payload(), destination)
            self.assertIsNotNone(fetch_gemini_snapshot(destination))
            with patch.dict(os.environ, {"QUOTA_WATCH_CACHE_DIR": directory}):
                with patch("quota_watch.cli.fetch_claude_snapshot"), patch("quota_watch.cli.fetch_codex_snapshot"):
                    self.assertEqual(cli.collect_snapshots(1.0)[-1].provider, "gemini")

    def _rewrite(self, destination: Path, age: timedelta, resets_in: int) -> None:
        payload = json.loads(destination.read_text(encoding="utf-8"))
        payload["captured_at"] = (datetime.now(timezone.utc) - age).isoformat()
        for bucket in payload["buckets"]:
            for window in bucket["windows"]:
                window["resets_at"] = int(time.time()) + resets_in
        destination.write_text(json.dumps(payload), encoding="utf-8")

    def test_idle_cache_reports_age_before_becoming_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "gemini.json"
            ingest_statusline(_payload(), destination)

            self._rewrite(destination, timedelta(hours=3), 3600)
            idle = read_cached_snapshot(destination)
            self.assertEqual(idle.status, "available")
            self.assertIn("3 hours old", idle.message)

            self._rewrite(destination, timedelta(days=2), 3600)
            self.assertEqual(read_cached_snapshot(destination).status, "stale")

    def test_reset_windows_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "gemini.json"
            ingest_statusline(_payload(), destination)
            self._rewrite(destination, timedelta(hours=6), -10)

            snapshot = read_cached_snapshot(destination)
            self.assertEqual(snapshot.status, "stale")
            self.assertEqual(snapshot.buckets, [])
            self.assertIn("reset", snapshot.message)

    def test_invalid_cache_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "gemini.json"
            destination.write_text("{", encoding="utf-8")
            self.assertEqual(read_cached_snapshot(destination).status, "unavailable")


class IngestCommandTests(unittest.TestCase):
    def _run(self, text: str, directory: str) -> str:
        stdin = io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8")
        stdout = io.StringIO()
        with patch.dict(os.environ, {"QUOTA_WATCH_CACHE_DIR": directory}):
            with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
                self.assertEqual(cli.main(["gemini-ingest"]), 0)
        return stdout.getvalue()

    def test_prints_compact_status_and_writes_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = self._run(json.dumps(_payload()), directory)
            self.assertIn("Gemini 5h 10%", output)
            self.assertTrue((Path(directory) / "gemini.json").exists())

    def test_stays_quiet_before_quota_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self._run('{"agent_state":"initializing"}', directory), "")
            self.assertFalse((Path(directory) / "gemini.json").exists())


class NativeSetupTests(unittest.TestCase):
    def test_replaces_disabled_placeholder_and_keeps_default_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"useG1Credits": False, "statusLine": {"type": "", "command": "", "enabled": False}}),
                encoding="utf-8",
            )
            changed, _, entry = configure_statusline(path)

            self.assertTrue(changed)
            settings = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(settings["statusLine"], entry)
            self.assertFalse(settings["useG1Credits"])
            self.assertEqual(entry["type"], "command")
            self.assertTrue(entry["enabled"])
            self.assertTrue(entry["stack_with_default"])
            self.assertIn("gemini-ingest", entry["command"])
            self.assertTrue(path.with_name("settings.json.quota-watch.bak").exists())

            changed, message, _ = configure_statusline(path)
            self.assertFalse(changed)
            self.assertIn("already", message)

    def test_leaves_custom_statusline_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = json.dumps({"statusLine": {"type": "command", "command": "~/line.sh"}})
            path.write_text(original, encoding="utf-8")

            changed, message, entry = configure_statusline(path)
            self.assertFalse(changed)
            self.assertIn("unchanged", message)
            self.assertIn("gemini-ingest", entry["command"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_non_object_statusline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"statusLine": true}', encoding="utf-8")
            with self.assertRaises(GeminiError):
                configure_statusline(path)


INGEST = "/mnt/c/Users/admin/.local/bin/quota.exe gemini-ingest"


class WslSetupTests(unittest.TestCase):
    def test_creates_wrapper_when_no_statusline_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            changed, _, entry = configure_wsl_gemini_files(home, "/home/will", INGEST)

            self.assertTrue(changed)
            self.assertEqual(entry["command"], "sh /home/will/.gemini/antigravity-cli/quota-watch-statusline.sh")
            self.assertTrue(entry["enabled"])
            self.assertTrue(entry["stack_with_default"])
            agy = home / ".gemini" / "antigravity-cli"
            self.assertEqual(json.loads((agy / "settings.json").read_text(encoding="utf-8"))["statusLine"], entry)
            wrapper = (agy / "quota-watch-statusline.sh").read_text(encoding="utf-8")
            self.assertIn(INGEST, wrapper)

            changed, message, _ = configure_wsl_gemini_files(home, "/home/will", INGEST)
            self.assertFalse(changed)
            self.assertIn("already", message)

    def test_wraps_and_backs_up_existing_statusline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            agy = home / ".gemini" / "antigravity-cli"
            agy.mkdir(parents=True)
            original = {"statusLine": {"type": "command", "command": "~/.gemini/hud.sh", "padding": 1}}
            (agy / "settings.json").write_text(json.dumps(original), encoding="utf-8")

            changed, _, entry = configure_wsl_gemini_files(home, "/home/will", INGEST)

            self.assertTrue(changed)
            self.assertEqual(entry["padding"], 1)
            self.assertNotIn("stack_with_default", entry)
            self.assertTrue((agy / "settings.json.quota-watch-wsl.bak").exists())
            wrapper = (agy / "quota-watch-statusline.sh").read_text(encoding="utf-8")
            self.assertIn("sh -c '~/.gemini/hud.sh'", wrapper)

    def test_dry_run_and_unrelated_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            changed, message, _ = configure_wsl_gemini_files(home, "/home/will", INGEST, dry_run=True)
            self.assertTrue(changed)
            self.assertIn("Dry run", message)
            self.assertFalse((home / ".gemini").exists())

            agy = home / ".gemini" / "antigravity-cli"
            agy.mkdir(parents=True)
            (agy / "quota-watch-statusline.sh").write_text("echo mine\n", encoding="utf-8")
            with self.assertRaises(GeminiError):
                configure_wsl_gemini_files(home, "/home/will", INGEST)

    @unittest.skipIf(os.name == "nt", "the wrapper is a POSIX shell script")
    def test_wrapper_forwards_at_most_once_per_interval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            received = home / "received.log"
            fake = home / "ingest.sh"
            fake.write_text(f'cat >> "{received}"\necho >> "{received}"\necho "Gemini 5h 10%"\n', encoding="utf-8")
            configure_wsl_gemini_files(home, str(home), f"sh {fake}")
            wrapper = home / ".gemini" / "antigravity-cli" / "quota-watch-statusline.sh"
            last = wrapper.with_name("quota-watch-statusline.last")

            def run(text: str) -> str:
                return subprocess.run(
                    ["sh", str(wrapper)], input=text, capture_output=True, text=True, timeout=10, check=True
                ).stdout

            run('{"n":1}')
            deadline = time.monotonic() + 5
            while not last.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(run('{"n":2}'), "Gemini 5h 10%\n")
            time.sleep(0.2)
            self.assertEqual(received.read_text(encoding="utf-8"), '{"n":1}\n')


if __name__ == "__main__":
    unittest.main()
