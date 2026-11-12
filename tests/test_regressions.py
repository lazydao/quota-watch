import io
import json
import tempfile
import time
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

from quota_watch import cli
from quota_watch.claude import (
    ClaudeError,
    _write_snapshot,
    fetch_claude_snapshot,
    ingest_statusline,
    parse_oauth_usage,
    read_cached_snapshot,
)
from quota_watch.codex import _process_command, _wait_for_id
from quota_watch.models import unavailable_snapshot


class StatuslineIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name) / "claude.json"
        self.reset = int(time.time()) + 3600

    def payload(self, used: float, resets_at: int | None = None) -> dict:
        return {"rate_limits": {"five_hour": {
            "used_percentage": used, "resets_at": self.reset if resets_at is None else resets_at,
        }}}

    def test_plan_is_looked_up_once_and_kept(self) -> None:
        with patch("quota_watch.claude.query_subscription_type", return_value="max") as lookup:
            for _ in range(4):
                snapshot = ingest_statusline(self.payload(10), self.cache)
        lookup.assert_called_once_with()
        self.assertEqual(snapshot.buckets[0].plan_type, "max")
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["buckets"][0]["plan_type"], "max")

    def test_unknown_plan_lookup_is_throttled(self) -> None:
        with patch("quota_watch.claude.query_subscription_type", return_value=None) as lookup:
            for _ in range(4):
                ingest_statusline(self.payload(10), self.cache)
        lookup.assert_called_once_with()

    def test_idle_session_does_not_replace_newer_usage(self) -> None:
        _write_snapshot(self.cache, parse_oauth_usage({"five_hour": {"utilization": 80, "resets_at": self.reset}}))
        with patch("quota_watch.claude.query_subscription_type", return_value=None):
            same_window = ingest_statusline(self.payload(10, self.reset + 1), self.cache)
            older_window = ingest_statusline(self.payload(95, self.reset - 3000), self.cache)
            grown = ingest_statusline(self.payload(85), self.cache)
            next_window = ingest_statusline(self.payload(2, self.reset + 18_000), self.cache)
        self.assertEqual(same_window.buckets[0].windows[0].used_percent, 80)
        self.assertEqual(older_window.buckets[0].windows[0].used_percent, 80)
        self.assertEqual(grown.buckets[0].windows[0].used_percent, 85)
        self.assertEqual(next_window.buckets[0].windows[0].used_percent, 2)

    def test_ingest_reads_utf8_and_reports_unwritable_cache(self) -> None:
        raw = json.dumps({"cwd": "C:\\用户\\张\\项目", **self.payload(12)}, ensure_ascii=False).encode("utf-8")
        output = io.StringIO()
        with patch.dict("os.environ", {"QUOTA_WATCH_CACHE_DIR": str(self.cache.parent)}):
            with patch("quota_watch.claude.query_subscription_type", return_value=None):
                with patch.object(cli.sys, "stdin", Mock(buffer=io.BytesIO(raw))):
                    with patch.object(cli.sys, "stdout", output):
                        self.assertEqual(cli.run_claude_ingest(), 0)
                with patch.object(cli.sys, "stdin", Mock(buffer=io.BytesIO(raw))):
                    with patch.object(cli.sys, "stdout", output):
                        with patch("quota_watch.cli.ingest_statusline", side_effect=PermissionError("locked")):
                            self.assertEqual(cli.run_claude_ingest(), 0)
        first, second = output.getvalue().splitlines()
        self.assertEqual(first, "Claude 5h 12%")
        self.assertIn("locked", second)
        self.assertEqual(read_cached_snapshot(self.cache, enrich_plan=False).buckets[0].windows[0].used_percent, 12)


class ActiveRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name) / "claude.json"
        self.state = Path(directory.name) / "refresh.json"
        for target, kwargs in (
            ("quota_watch.claude.query_oauth_usage_wsl", {"side_effect": ClaudeError("offline")}),
            ("quota_watch.claude._desktop_history_paths", {"return_value": []}),
        ):
            patcher = patch(target, **kwargs)
            self.addCleanup(patcher.stop)
            patcher.start()

    def fetch(self):
        return fetch_claude_snapshot(destination=self.cache, refresh_state=self.state)

    def test_failure_reason_is_kept_during_cooldown(self) -> None:
        with patch("quota_watch.claude.query_oauth_usage_windows", side_effect=ClaudeError("token expired")) as query:
            first = self.fetch()
            second = self.fetch()
        query.assert_called_once()
        self.assertIn("token expired", first.message)
        self.assertEqual((second.status, second.message), ("unavailable", first.message))

    def test_unwritable_cache_keeps_the_fresh_result(self) -> None:
        usage = {"five_hour": {"utilization": 33, "resets_at": int(time.time()) + 3600}}
        with patch("quota_watch.claude.query_oauth_usage_windows", return_value=usage):
            with patch("quota_watch.claude._atomic_json_write", side_effect=PermissionError("locked")):
                snapshot = self.fetch()
        self.assertEqual((snapshot.status, snapshot.buckets[0].windows[0].used_percent), ("available", 33))


class CliAndCodexTests(unittest.TestCase):
    def test_json_output_is_ascii_only(self) -> None:
        output = io.StringIO()
        snapshots = [unavailable_snapshot("codex", "找不到 C:\\用户\\codex.exe")]
        with patch("quota_watch.cli.collect_snapshots", return_value=snapshots):
            with patch.object(cli.sys, "stdout", output):
                cli.run_once(Mock(), 8, as_json=True)
        self.assertTrue(output.getvalue().isascii())
        self.assertEqual(json.loads(output.getvalue())["providers"][0]["message"], snapshots[0].message)

    def test_batch_launcher_path_with_spaces_stays_quoted(self) -> None:
        launcher = PureWindowsPath(r"C:\Program Files\nodejs\codex.cmd")
        with patch("quota_watch.codex.os.name", "nt"):
            command = _process_command(launcher)
        self.assertEqual(command, r'cmd.exe /d /s /c ""C:\Program Files\nodejs\codex.cmd" app-server"')

    def test_non_object_output_lines_are_ignored(self) -> None:
        import queue

        output: "queue.Queue[str]" = queue.Queue()
        for line in ("42", "[]", '"text"', '{"id":2,"result":{"ok":true}}'):
            output.put(line)
        process = Mock(poll=Mock(return_value=0), returncode=0)
        self.assertEqual(_wait_for_id(output, 2, time.monotonic() + 2, process), {"ok": True})


if __name__ == "__main__":
    unittest.main()
