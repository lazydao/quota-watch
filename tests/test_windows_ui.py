import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


class WindowsUiTests(unittest.TestCase):
    def test_popup_uses_transparent_window_without_rectangular_shadow(self) -> None:
        xaml_path = Path(__file__).parents[1] / "windows" / "QuotaWatch.Tray" / "MainWindow.xaml"
        window = ElementTree.parse(xaml_path).getroot()
        namespace = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        outer_border = window.find(f"{{{namespace}}}Border")

        self.assertEqual(window.get("AllowsTransparency"), "True")
        self.assertEqual(window.get("Background"), "Transparent")
        self.assertIsNotNone(outer_border)
        self.assertEqual(outer_border.get("CornerRadius"), "12")
        self.assertIsNone(outer_border.find(f"{{{namespace}}}Border.Effect"))


if __name__ == "__main__":
    unittest.main()
