"""Real legacy refresh -> modern stats -> callback chain, with fake game I/O."""
import unittest
import threading
from unittest.mock import Mock, patch

import numpy as np

from imprint_decompose.config import load_feature_config
from imprint_decompose.controller_v2 import ImprintDecomposeController
from imprint_decompose.models import ImprintFrameAnalysis, ImprintState
from tower_bot.models import BotError, Rect, WindowInfo
from test_auto_mode import inventory


class ModeStatusTests(unittest.TestCase):
    def setUp(self):
        cfg, feature = load_feature_config()
        self.c = ImprintDecomposeController(cfg, feature, dry_run=True)
        self.c._ensure_worker = Mock()  # no background worker or native input
        self.c.locator = Mock()
        self.window = WindowInfo(123, 'offline', Rect(0, 0, 550, 1020))
        self.c.locator.find.return_value = self.window
        self.c.capture = Mock()
        self.c.capture.grab_window.return_value = np.zeros((1020, 550, 3), np.uint8)
        self.c.detector = Mock()
        self.analysis = ImprintFrameAnalysis(ImprintState.LIST, None, .1, 0, 1, 15,
                                             False, False, 'offline mode regression')
        self.c.detector.analyze.return_value = self.analysis
        self.c.input.can_act = Mock(return_value=False)
        self.events = []
        self.c.on_stats = self.events.append

    def tearDown(self):
        self.c.on_stats = None
        self.c.shutdown()

    def assert_modes(self, automatic):
        self.assertTrue(self.events)
        self.assertTrue(all(s.auto_mode == automatic for s in self.events))
        self.assertEqual(self.c.stats.auto_mode, automatic)
        self.assertEqual(self.c._scan_mode_enabled, automatic)
        self.assertFalse(self.c._auto_enabled)

    def test_every_real_refresh_snapshot_keeps_auto_mode_and_progress(self):
        self.c.set_auto_mode(True)
        self.c.start()
        run = self.c._scan_run
        run.candidates, run.scrolls, run.processed, run.pass_number = 7, 9, 3, 2
        self.events.clear()
        for _ in range(8):
            self.c._update_stats(self.analysis, self.window)
            self.c.refresh_window_info()
            self.c._sync_scan_stats(run)
        self.assert_modes(True)
        self.assertTrue(all((s.auto_candidates, s.auto_scrolled, s.auto_processed, s.auto_pass)
                            == (7, 9, 3, 2) for s in self.events))

    def test_complete_tick_and_window_poll_never_emit_false_manual_mode(self):
        self.c.set_auto_mode(True)
        self.c.start()
        self.events.clear()
        with patch('imprint_decompose.auto_controller.read_inventory', return_value=inventory(5)):
            for _ in range(8):
                self.c._tick()
                self.c.refresh_window_info()
        self.assert_modes(True)
        self.assertEqual(self.c.input.backend.clicks, [])
        self.assertEqual(self.c.input.backend.scrolls, [])

    def test_stop_pause_restart_and_manual_mode_refresh_are_consistent(self):
        self.c.set_auto_mode(True)
        for action in (self.c.start, self.c.pause, self.c.start, self.c.stop,
                       self.c.start, self.c.emergency_pause):
            self.events.clear()
            action()
            self.c._update_stats(self.analysis, self.window)
            self.c.refresh_window_info()
            self.assert_modes(True)
        self.c.set_auto_mode(False)
        self.events.clear()
        for _ in range(3):
            self.c._update_stats(self.analysis, self.window)
            self.c.refresh_window_info()
        self.assert_modes(False)

    def test_reselecting_active_mode_does_not_stop_or_revoke_run(self):
        self.c.set_auto_mode(True)
        self.c.start()
        run = self.c._scan_run
        self.c.set_auto_mode(True)
        self.assertTrue(self.c.enabled.is_set())
        self.assertTrue(run.valid)
        self.assertIs(self.c._scan_run, run)

    def test_safety_stop_before_sync_does_not_show_manual_mode(self):
        self.c.set_auto_mode(True)
        self.c.start()
        self.events.clear()
        with patch('imprint_decompose.auto_controller.read_inventory', side_effect=ValueError('offline failure')), \
             patch.object(self.c, '_save_debug', autospec=True):
            with self.assertRaisesRegex(BotError, 'offline failure'):
                self.c._tick()
        self.c.refresh_window_info()
        self.assert_modes(True)
        self.assertEqual(self.events[-1].auto_phase, '保护暂停')
        self.assertFalse(self.c.enabled.is_set())

    def test_confirmation_setting_is_independent_across_real_refreshes(self):
        self.c.set_auto_mode(True)
        self.c.set_confirmation_mode('manual')
        self.c.start()
        self.events.clear()
        for _ in range(3):
            self.c._update_stats(self.analysis, self.window)
            self.c._sync_scan_stats(self.c._scan_run)
        self.assert_modes(True)
        self.assertTrue(all(s.confirmation_mode == '手动确认' for s in self.events))

    def test_window_poll_during_legacy_refresh_cannot_publish_partial_mode(self):
        self.c.set_auto_mode(True)
        self.events.clear()
        inside, release, polling = threading.Event(), threading.Event(), threading.Event()
        failures = []

        def hold_diagnostic(_analysis):
            inside.set()
            if not release.wait(2):
                raise AssertionError('test did not release legacy refresh')

        def capture_failure(action):
            try:
                action()
            except BaseException as exc:
                failures.append(exc)

        def poll():
            polling.set()
            self.c.refresh_window_info()

        with patch.object(self.c, '_log_list_diagnostic', side_effect=hold_diagnostic):
            worker = threading.Thread(target=lambda: capture_failure(
                lambda: self.c._update_stats(self.analysis, self.window)))
            observer = threading.Thread(target=lambda: capture_failure(poll))
            worker.start()
            try:
                self.assertTrue(inside.wait(2))
                observer.start()
                self.assertTrue(polling.wait(2))
            finally:
                release.set()
                worker.join(2)
                if observer.ident is not None:
                    observer.join(2)
            self.assertFalse(worker.is_alive() or observer.is_alive(), 'stats lock deadlocked')
        self.assertEqual(failures, [])
        self.assert_modes(True)


if __name__ == '__main__':
    unittest.main()
