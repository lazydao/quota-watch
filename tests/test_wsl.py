import json
import tempfile
import unittest
from pathlib import Path

from quota_watch.wsl import configure_wsl_files


class WslSetupTests(unittest.TestCase):
    def test_creates_statusline_when_none_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            changed, _, entry = configure_wsl_files(
                home,
                "/home/will",
                "/mnt/c/Users/admin/.local/bin/quota.exe claude-ingest",
            )

            self.assertTrue(changed)
            self.assertEqual(entry["refreshInterval"], 5)
            settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["statusLine"], entry)
            wrapper = (home / ".claude" / "quota-watch-statusline.sh").read_text(encoding="utf-8")
            self.assertIn("quota.exe claude-ingest", wrapper)
            self.assertIn("quota_output", wrapper)

    def test_wraps_and_backs_up_existing_statusline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            claude = home / ".claude"
            claude.mkdir()
            settings_path = claude / "settings.json"
            original = {
                "theme": "dark",
                "statusLine": {
                    "type": "command",
                    "command": "bash /home/will/.claude/original.sh",
                    "padding": 2,
                },
            }
            settings_path.write_text(json.dumps(original), encoding="utf-8")
            (claude / "original.sh").write_text("input=$(cat)\nprintf '%s' \"$input\"\n", encoding="utf-8")

            changed, _, entry = configure_wsl_files(
                home,
                "/home/will",
                "/mnt/c/Users/admin/.local/bin/quota.exe claude-ingest",
            )

            self.assertTrue(changed)
            self.assertEqual(entry["padding"], 2)
            self.assertTrue((claude / "settings.json.quota-watch-wsl.bak").exists())
            wrapper = (claude / "quota-watch-statusline.sh").read_text(encoding="utf-8")
            self.assertIn("bash /home/will/.claude/original.sh", wrapper)
            updated = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["theme"], "dark")

    def test_detects_existing_forwarder_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            claude = home / ".claude"
            claude.mkdir()
            settings_path = claude / "settings.json"
            original = {
                "statusLine": {
                    "type": "command",
                    "command": "bash /home/will/.claude/statusline-command.sh",
                }
            }
            settings_path.write_text(json.dumps(original), encoding="utf-8")
            script = claude / "statusline-command.sh"
            script.write_text(
                "input=$(cat)\nprintf '%s' \"$input\" | /mnt/c/Users/admin/.local/bin/quota.exe claude-ingest\n",
                encoding="utf-8",
            )

            changed, message, _ = configure_wsl_files(
                home,
                "/home/will",
                "/mnt/c/Users/admin/.local/bin/quota.exe claude-ingest",
            )

            self.assertFalse(changed)
            self.assertIn("already", message)
            self.assertFalse((claude / "quota-watch-statusline.sh").exists())

    def test_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            changed, message, _ = configure_wsl_files(
                home,
                "/home/will",
                "/mnt/c/Users/admin/.local/bin/quota.exe claude-ingest",
                dry_run=True,
            )

            self.assertTrue(changed)
            self.assertIn("Dry run", message)
            self.assertFalse((home / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
