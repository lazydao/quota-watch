import io
import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console

from quota_watch.cli import run_watch


class WatchTests(unittest.TestCase):
    def test_watch_uses_manual_alternate_screen_refresh(self) -> None:
        live = MagicMock()
        live_context = MagicMock()
        live_context.__enter__.return_value = live

        with patch("quota_watch.cli.collect_snapshots", return_value=[]):
            with patch("quota_watch.cli.Live", return_value=live_context) as live_type:
                with patch("quota_watch.cli.time.sleep", side_effect=KeyboardInterrupt):
                    result = run_watch(Console(file=io.StringIO()), interval=30, codex_timeout=8)

        self.assertEqual(result, 0)
        live_type.assert_called_once()
        self.assertTrue(live_type.call_args.kwargs["screen"])
        self.assertFalse(live_type.call_args.kwargs["auto_refresh"])
        live.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
