"""Check the Windows input boundary with every native input call intercepted."""
import unittest
from unittest.mock import Mock, patch

import pyautogui
import win32con

from imprint_decompose.config import load_feature_config
from tower_bot.input_controller import InputController, PyAutoGuiBackend
from tower_bot.models import ActionType, BotError, Point, Rect, WindowInfo


class NativeScrollTests(unittest.TestCase):
    def setUp(self):
        self.enabled = True
        self.backend = PyAutoGuiBackend(lambda: self.enabled)
        self.move = self.enterContext(patch('pyautogui.moveTo'))
        self.failsafe = self.enterContext(patch('pyautogui.failSafeCheck'))
        self.event = self.enterContext(patch('win32api.mouse_event'))
        # The old PyAutoGUI path sends raw +/-1 and can swallow OS errors.
        self.enterContext(patch('pyautogui.scroll', side_effect=AssertionError('legacy wheel path')))

    def test_notches_are_signed_windows_wheel_deltas(self):
        for notches in (1, -1, 3, -3):
            with self.subTest(notches=notches):
                self.backend.scroll(1387, 977, notches)
                self.move.assert_called_with(1387, 977, duration=0.05)
                self.event.assert_called_with(win32con.MOUSEEVENTF_WHEEL, 0, 0, notches * 120, 0)
        self.assertEqual(self.event.call_count, 4)

    def test_disabled_before_move_sends_nothing(self):
        self.enabled = False
        with self.assertRaises(BotError):
            self.backend.scroll(1387, 977, -1)
        self.move.assert_not_called()
        self.event.assert_not_called()

    def test_pause_during_move_prevents_wheel(self):
        def pause(*args, **kwargs):
            self.enabled = False
        self.move.side_effect = pause
        with self.assertRaises(BotError):
            self.backend.scroll(1387, 977, -1)
        self.event.assert_not_called()

    def test_failsafe_prevents_native_wheel(self):
        self.failsafe.side_effect = pyautogui.FailSafeException('test corner')
        with self.assertRaises(pyautogui.FailSafeException):
            self.backend.scroll(1387, 977, -1)
        self.event.assert_not_called()

    def test_native_failure_is_not_silently_acknowledged(self):
        self.event.side_effect = OSError('test input failure')
        with self.assertRaisesRegex(OSError, 'test input failure'):
            self.backend.scroll(1387, 977, -1)

    def test_reference_scroll_reaches_native_boundary_with_correct_units(self):
        cfg, _ = load_feature_config()
        window = WindowInfo(123, 'test', Rect(1112, 168, 1662, 1168))
        locator = Mock()
        locator.current_rect.return_value = window.rect
        locator.focus.return_value = locator.is_foreground.return_value = True
        on_action = Mock()
        control = InputController(cfg, locator, backend=self.backend,
                                  enabled_check=lambda: self.enabled, on_action=on_action)
        self.assertEqual(control.scroll_reference(window, Point(275, 825), clicks=-1,
                         action=ActionType.SCROLL_IMPRINT_LIST, reason='regression'), Point(1387, 977))
        self.move.assert_called_once_with(1387, 977, duration=0.05)
        self.event.assert_called_once_with(win32con.MOUSEEVENTF_WHEEL, 0, 0, -120, 0)
        on_action.assert_called_once()

    def test_failed_dispatch_does_not_mark_action_success(self):
        cfg, _ = load_feature_config()
        window = WindowInfo(123, 'test', Rect(0, 0, 550, 1020))
        locator = Mock()
        locator.current_rect.return_value = window.rect
        locator.focus.return_value = locator.is_foreground.return_value = True
        on_action = Mock()
        control = InputController(cfg, locator, backend=self.backend, on_action=on_action)
        self.event.side_effect = OSError('test input failure')
        with self.assertRaises(OSError):
            control.scroll_reference(window, Point(275, 825), clicks=-1,
                                     action=ActionType.SCROLL_IMPRINT_LIST, reason='regression')
        self.assertEqual(control.last_action_type, ActionType.NONE)
        on_action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
