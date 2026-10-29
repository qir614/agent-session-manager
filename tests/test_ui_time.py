import unittest
from agent_session_manager.gui import display_time, responsive_font_size

class UiTimeTests(unittest.TestCase):
    def test_seconds_and_milliseconds_show_same_local_date(self):
        self.assertEqual(display_time("1791622421"), display_time("1791622421000"))
        self.assertRegex(display_time("1791622421"), r"^2026-10-10 \d{2}:\d{2}$")

    def test_iso_and_invalid_time(self):
        self.assertRegex(display_time("2026-10-10T03:00:00Z"), r"^2026-10-10 ")
        self.assertEqual(display_time("unknown"), "unknown")
        self.assertEqual(display_time(""), "—")

class ResponsiveFontTests(unittest.TestCase):
    def test_large_window_increases_text_size(self):
        self.assertEqual(responsive_font_size(1420, 900), 12)
        self.assertEqual(responsive_font_size(2840, 1800), 24)
        self.assertGreater(responsive_font_size(3456, 2160), responsive_font_size(1420, 900))

    def test_small_and_extreme_windows_remain_readable(self):
        self.assertEqual(responsive_font_size(1060, 680), 12)
        self.assertEqual(responsive_font_size(10000, 10000), 28)
