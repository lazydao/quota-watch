import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("set_version", REPOSITORY / "scripts" / "set_version.py")
set_version_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(set_version_module)


class SetVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, _, _ in set_version_module.TARGETS:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPOSITORY / name, self.root / name)

    def read(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def test_updates_every_version_field_and_is_repeatable(self) -> None:
        changed = set_version_module.set_version(self.root, "9.8.7")
        self.assertEqual(changed, [name for name, _, _ in set_version_module.TARGETS])
        self.assertIn('\nversion = "9.8.7"\n', self.read("pyproject.toml"))
        self.assertIn('__version__ = "9.8.7"', self.read("src/quota_watch/__init__.py"))
        self.assertIn("<Version>9.8.7</Version>", self.read("windows/QuotaWatch.Tray/QuotaWatch.Tray.csproj"))
        self.assertEqual(self.read("README.md").count('"v9.8.7"'), 2)
        self.assertEqual(set_version_module.set_version(self.root, "9.8.7"), [])

    def test_rejects_invalid_versions_without_writing(self) -> None:
        before = self.read("pyproject.toml")
        for version in ("v1.2.3", "1.2", "1.2.3-rc1", ""):
            with self.assertRaises(ValueError):
                set_version_module.set_version(self.root, version)
        self.assertEqual(self.read("pyproject.toml"), before)

    def test_missing_field_changes_nothing(self) -> None:
        readme = self.root / "README.md"
        readme.write_text("no version here\n", encoding="utf-8")
        before = self.read("pyproject.toml")
        with self.assertRaises(ValueError):
            set_version_module.set_version(self.root, "9.8.7")
        self.assertEqual(self.read("pyproject.toml"), before)

    def test_repository_versions_agree(self) -> None:
        from quota_watch import __version__

        self.assertEqual(set_version_module.set_version(self.root, __version__), [])


if __name__ == "__main__":
    unittest.main()
