import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quota_watch.codex import find_codex_executable, parse_rate_limits


class CodexParserTests(unittest.TestCase):
    def test_prefers_official_standalone_install(self) -> None:
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
                    self.assertEqual(find_codex_executable(), standalone)

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
