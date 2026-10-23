import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quota_watch.codex import find_codex_executable, parse_rate_limits, query_rate_limits


class CodexParserTests(unittest.TestCase):
    def test_prefers_windows_desktop_backend_over_standalone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standalone = root / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
            desktop = root / "OpenAI" / "Codex" / "bin" / "codex.exe"
            standalone.parent.mkdir(parents=True)
            desktop.parent.mkdir(parents=True)
            standalone.touch()
            desktop.touch()

            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(root), "QUOTA_WATCH_CODEX_PATH": ""},
                clear=False,
            ):
                with patch("quota_watch.codex.shutil.which", return_value=str(desktop)):
                    self.assertEqual(find_codex_executable(), desktop)

    def test_prefers_newest_versioned_app_backend_over_legacy_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend_dir = root / "OpenAI" / "Codex" / "bin"
            old = backend_dir / "old-version" / "codex.exe"
            new = backend_dir / "new-version" / "codex.exe"
            legacy = backend_dir / "codex.exe"
            for path in (old, new, legacy):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            os.utime(old, (100, 100))
            os.utime(new, (200, 200))
            os.utime(legacy, (300, 300))
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root), "QUOTA_WATCH_CODEX_PATH": ""}):
                with patch("quota_watch.codex.shutil.which", return_value=None):
                    self.assertEqual(find_codex_executable(), new)

    def test_standalone_remains_available_without_desktop_app(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standalone = root / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
            standalone.parent.mkdir(parents=True)
            standalone.touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root), "QUOTA_WATCH_CODEX_PATH": ""}):
                with patch("quota_watch.codex.shutil.which", return_value=None):
                    self.assertEqual(find_codex_executable(), standalone)

    def test_explicit_path_still_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "custom.exe"
            override.touch()
            with patch.dict(os.environ, {"QUOTA_WATCH_CODEX_PATH": str(override)}):
                self.assertEqual(find_codex_executable(), override)

    def test_query_sends_only_initialization_and_read_only_rate_limit_method(self) -> None:
        process = Mock()
        process.stdout = io.StringIO()
        process.stderr = io.StringIO()
        process.poll.return_value = None
        result = {"rateLimits": {"limitId": "codex"}}
        executable = Path("codex.exe")
        with patch("quota_watch.codex.subprocess.Popen", return_value=process) as start:
            with patch("quota_watch.codex._start_reader"):
                with patch("quota_watch.codex._wait_for_id", side_effect=[{}, result]):
                    self.assertEqual(query_rate_limits(executable=executable), result)
        messages = [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]
        self.assertEqual([message["method"] for message in messages], [
            "initialize", "initialized", "account/rateLimits/read",
        ])
        self.assertEqual(start.call_args.args[0], [str(executable), "app-server"])
        process.terminate.assert_called_once()

    def test_parses_multiple_buckets_and_window_durations(self) -> None:
        snapshot = parse_rate_limits(
            {
                "rateLimits": {"limitId": "codex"},
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitId": "codex",
                        "limitName": None,
                        "primary": {
                            "usedPercent": 11,
                            "windowDurationMins": 300,
                            "resetsAt": 1_800_000_000,
                        },
                        "secondary": {
                            "usedPercent": 22,
                            "windowDurationMins": 10_080,
                            "resetsAt": 1_800_100_000,
                        },
                        "planType": "pro",
                    },
                    "codex_spark": {
                        "limitId": "codex_spark",
                        "limitName": "Codex Spark",
                        "primary": {
                            "usedPercent": 3,
                            "windowDurationMins": 10_080,
                            "resetsAt": None,
                        },
                        "secondary": None,
                    },
                },
            }
        )

        self.assertEqual(snapshot.status, "available")
        self.assertEqual([bucket.name for bucket in snapshot.buckets], ["Codex", "Codex Spark"])
        self.assertEqual([window.label for window in snapshot.buckets[0].windows], ["5h", "1w"])
        self.assertEqual(snapshot.buckets[0].plan_type, "pro")

    def test_falls_back_to_single_bucket_view(self) -> None:
        snapshot = parse_rate_limits(
            {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 50, "windowDurationMins": 60, "resetsAt": 123},
                    "secondary": None,
                }
            }
        )
        self.assertEqual(len(snapshot.buckets), 1)
        self.assertEqual(snapshot.buckets[0].windows[0].label, "1h")


if __name__ == "__main__":
    unittest.main()
