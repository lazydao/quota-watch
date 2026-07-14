import unittest
from unittest.mock import MagicMock, PropertyMock, patch

from rich.console import Console, ConsoleDimensions

from quota_watch.cli import run_watch


class WatchTests(unittest.TestCase):
    def test_watch_uses_manual_alternate_screen_refresh(self) -> None:
        live = MagicMock()
        live_context = MagicMock()
        live_context.__enter__.return_value = live
        console = MagicMock(spec=Console)
        type(console).size = PropertyMock(
            side_effect=[ConsoleDimensions(80, 24), ConsoleDimensions(120, 40)]
        )

        with patch("quota_watch.cli.collect_snapshots", return_value=[]):
            with patch("quota_watch.cli.Live", return_value=live_context) as live_type:
                with patch("quota_watch.cli.time.monotonic", return_value=0):
                    with patch("quota_watch.cli.time.sleep", side_effect=[None, KeyboardInterrupt]):
                        result = run_watch(console, interval=30, codex_timeout=8)

        self.assertEqual(result, 0)
        live_type.assert_called_once()
        self.assertTrue(live_type.call_args.kwargs["screen"])
        self.assertFalse(live_type.call_args.kwargs["auto_refresh"])
        console.clear.assert_called_once_with()
        live.refresh.assert_called_once_with()
        live.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
