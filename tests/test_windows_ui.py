import struct
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path, PureWindowsPath


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

    def test_tray_executable_has_a_multi_size_application_icon(self) -> None:
        project_directory = Path(__file__).parents[1] / "windows" / "QuotaWatch.Tray"
        project = ElementTree.parse(project_directory / "QuotaWatch.Tray.csproj").getroot()
        application_icon = project.findtext("./PropertyGroup/ApplicationIcon")

        self.assertEqual(application_icon, r"Assets\QuotaWatch.ico")
        icon_path = project_directory.joinpath(*PureWindowsPath(application_icon).parts)
        icon_data = icon_path.read_bytes()
        reserved, image_type, image_count = struct.unpack_from("<HHH", icon_data)
        self.assertEqual((reserved, image_type), (0, 1))

        sizes = set()
        for index in range(image_count):
            entry_offset = 6 + index * 16
            width = icon_data[entry_offset] or 256
            height = icon_data[entry_offset + 1] or 256
            image_size, image_offset = struct.unpack_from("<II", icon_data, entry_offset + 8)
            self.assertEqual(width, height)
            self.assertEqual(icon_data[image_offset : image_offset + 8], b"\x89PNG\r\n\x1a\n")
            self.assertLessEqual(image_offset + image_size, len(icon_data))
            sizes.add(width)

        self.assertTrue({16, 32, 48, 256}.issubset(sizes))


if __name__ == "__main__":
    unittest.main()
