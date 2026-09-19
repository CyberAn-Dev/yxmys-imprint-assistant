import unittest
from types import SimpleNamespace

from imprint_decompose.ui_v2 import ImprintDecomposeUI


class WindowUsageStatusTests(unittest.TestCase):
    def setUp(self):
        self.ui = object.__new__(ImprintDecomposeUI)
        self.ui.controller = SimpleNamespace(
            cfg={
                'window': {
                    'reference_width': 550,
                    'reference_height': 1020,
                    'minimum_width': 500,
                    'minimum_height': 900,
                    'aspect_ratio_tolerance': 0.05,
                }
            }
        )

    def usage(self, size, window, vision):
        return self.ui._window_usage_status(size, window, vision)

    def test_matching_resolution_without_game_is_not_usable(self):
        self.assertEqual(
            self.usage('550×1020', '未找到窗口', 'UNKNOWN'),
            ('当前无法使用', False),
        )

    def test_stale_window_size_without_visual_match_is_not_usable(self):
        self.assertEqual(
            self.usage('550×1020', '已找到 hwnd=1378598', 'UNKNOWN'),
            ('当前无法使用', False),
        )

    def test_valid_window_resolution_and_visual_match_is_usable(self):
        self.assertEqual(
            self.usage('550×1020', '已找到 hwnd=1378598', 'LIST'),
            ('当前可以使用', True),
        )

    def test_invalid_resolution_is_not_usable(self):
        self.assertEqual(
            self.usage('800×600', '已找到 hwnd=1378598', 'DETAIL'),
            ('当前无法使用', False),
        )

    def test_current_resolution_is_displayed_from_detected_window(self):
        self.assertEqual(
            self.ui._format_current_resolution('511×940 px（比例正常，缩放≈0.93x）'),
            '当前分辨率：511×940',
        )

    def test_current_resolution_is_unknown_without_window(self):
        self.assertEqual(
            self.ui._format_current_resolution('-'),
            '当前分辨率：—',
        )


if __name__ == '__main__':
    unittest.main()
