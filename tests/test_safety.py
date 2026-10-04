"""Boundary, viewport and destructive-action protection regressions."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from imprint_decompose.config import load_feature_config
from imprint_decompose.controller_v2 import (
    ImprintDecomposeController, ImprintWindowLocator,
    enhancement_keep_reason, red_attribute_meets_threshold,
)
from tower_bot.input_controller import DryRunBackend, InputController
from tower_bot.models import ActionType, BotError, Point, Rect, WindowInfo


class ProtectionTests(unittest.TestCase):
    def test_escape_hotkey_registered(self):
        from imprint_decompose.hotkeys import ImprintHotkeyBridge
        cfg, feature = load_feature_config()
        controller = Mock(feature_cfg=feature)
        with patch('pynput.keyboard.GlobalHotKeys') as listener:
            self.assertTrue(ImprintHotkeyBridge(controller).start())
            mapping = listener.call_args.args[0]
            self.assertIn('<esc>', mapping)
            mapping['<esc>']()
            controller.emergency_pause.assert_called_once()

    def test_all_thresholds_include_equality_in_every_path(self):
        controller = object.__new__(ImprintDecomposeController)
        controller._auto_settings_lock = threading.Lock()
        for threshold in (10, 15, 20, 27):
            controller._enhancement_red_threshold = threshold
            for value in (threshold - 0.1, threshold, threshold + 1):
                analysis = SimpleNamespace(red_attribute_values=(value,))
                expected = value >= threshold
                self.assertEqual(controller._red_attribute_meets_threshold(analysis), expected)
                self.assertEqual(red_attribute_meets_threshold((value,), threshold), expected)
                reason = enhancement_keep_reason((value,), 0, threshold, ('storm', 'arc'), ('storm', 'arc'))
                self.assertEqual(reason is not None, expected)
                if reason:
                    self.assertIn('≥', reason)

    def test_unreadable_values_are_protected(self):
        self.assertIsNotNone(enhancement_keep_reason((), 1, 20, (), ()))

    def test_no_enhancement_still_protects_threshold_and_unreadable(self):
        for values, unreadable in (((20,), 0), ((), 1)):
            controller = object.__new__(ImprintDecomposeController)
            controller._auto_settings_lock = threading.Lock()
            controller._enhancement_red_threshold = 20
            controller._enhancement_is_enabled = lambda: False
            controller._count_red_threshold_match = Mock()
            controller._keep_detail = Mock()
            controller._record_operation = Mock()
            controller._emit_stats = Mock()
            controller.stats = SimpleNamespace()
            analysis = SimpleNamespace(
                detail=SimpleNamespace(right_ready=True, right_combination=('storm', 'arc'), right_filled_count=2),
                dismantle_ready=True, red_attribute_values=values, red_attribute_unreadable_count=unreadable)
            controller._handle_detail(None, None, analysis)
            controller._keep_detail.assert_not_called()
            controller._handle_detail(None, None, analysis)
            controller._keep_detail.assert_called_once()


class ViewportTests(unittest.TestCase):
    def setUp(self):
        self.cfg, _ = load_feature_config()

    def test_find_and_current_rect_agree_for_wide_and_normal_windows(self):
        for raw in ((100, 50, 1600, 1070), (100, 50, 650, 1070), (0, 0, 558, 999)):
            locator = ImprintWindowLocator(self.cfg)
            with patch('imprint_decompose.controller_v2.win32gui.EnumWindows', side_effect=lambda cb, _: cb(123, None)), \
                 patch('imprint_decompose.controller_v2.win32gui.IsWindowVisible', return_value=True), \
                 patch('imprint_decompose.controller_v2.win32gui.IsIconic', return_value=False), \
                 patch('imprint_decompose.controller_v2.win32gui.GetWindowText', return_value=self.cfg['window']['title_contains']), \
                 patch('imprint_decompose.controller_v2.win32gui.GetWindowRect', return_value=raw):
                found = locator.find()
                self.assertIsNotNone(found)
                self.assertEqual(found.rect, locator.current_rect(123))
                if raw[2] - raw[0] == 1500:
                    self.assertEqual(found.rect, Rect(575, 50, 1125, 1070))
                else:
                    self.assertEqual(found.rect, Rect(*raw))

    def test_pause_during_focus_does_not_send_click(self):
        enabled = [True]
        window = WindowInfo(hwnd=123, title='test', rect=Rect(0, 0, 550, 1020))
        locator = Mock()
        locator.current_rect.return_value = window.rect
        locator.is_foreground.return_value = True
        def focus(_):
            enabled[0] = False
            return True
        locator.focus.side_effect = focus
        backend = DryRunBackend()
        control = InputController(self.cfg, locator, backend=backend, enabled_check=lambda: enabled[0])
        with self.assertRaises(BotError):
            control.click_reference(window, Point(337, 870), action=ActionType.CLICK_DISMANTLE, reason='regression')
        self.assertEqual(backend.clicks, [])


if __name__ == '__main__':
    unittest.main()
