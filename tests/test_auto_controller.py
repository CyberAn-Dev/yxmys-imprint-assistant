"""Adapter tests use a recording backend only, never Windows input."""
import unittest
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from imprint_decompose.auto_mode import AutoSafetyError, Phase
from imprint_decompose.controller_v2 import ImprintDecomposeController
from imprint_decompose.config import load_feature_config
from imprint_decompose.models import ImprintState
from tower_bot.input_controller import DryRunBackend
from tower_bot.models import BotError, Rect, WindowInfo
from test_auto_mode import Rig, inventory, observation


class AutomaticAdapterTests(unittest.TestCase):
    def setUp(self):
        cfg, feature = load_feature_config()
        self.c = ImprintDecomposeController(cfg, feature, dry_run=True)
        self.c.dry_run = False  # allow workflow acknowledgement with fake I/O
        self.c._ensure_worker = Mock()  # never create a worker in these tests
        self.c._emit_stats = Mock()
        self.c._record_operation = Mock()
        self.c._update_stats = Mock()
        self.c.locator = Mock()
        self.c.input.locator = self.c.locator
        self.c.input.backend = DryRunBackend()
        self.c.input.can_act = Mock(return_value=True)
        self.window = WindowInfo(123, 'test', Rect(0, 0, 550, 1020))
        self.c.locator.current_rect.return_value = self.window.rect
        self.c.locator.find.return_value = self.window
        self.c.locator.focus.return_value = True
        self.c.locator.is_foreground.return_value = True
        self.c.capture = Mock()
        self.c.capture.is_fresh.return_value = True
        self.frame = np.zeros((1020, 550, 3), np.uint8)
        self.c.capture.grab_window.return_value = self.frame
        self.c.detector = Mock()
        self.c.set_auto_mode(True)
        self.c.start()

    def tearDown(self):
        self.c.shutdown()

    def prepare(self, kind='dismantle'):
        rig = Rig(3)
        result = rig.enhance()
        intent = rig.command(observation(ImprintState.DETAIL, result))
        if kind == 'confirm':
            rig.ack(intent)
            intent = rig.command(observation(ImprintState.CONFIRM))
        self.c._scan_run = rig.run
        analysis = observation(ImprintState.DETAIL, result) if kind == 'dismantle' else observation(ImprintState.CONFIRM)
        self.c.detector.analyze.return_value = analysis
        return rig, intent, analysis

    def test_auto_enables_enhancement_without_enabling_legacy_scanner(self):
        self.assertTrue(self.c.stats.auto_mode)
        self.assertTrue(self.c._enhancement_enabled)
        self.assertFalse(self.c._auto_enabled)
        with self.assertRaises(ValueError):
            self.c.set_enhancement_settings(False, 1, 20)

    def test_top_jump_reaches_backend_as_one_large_upward_event(self):
        rig = Rig()
        intent = rig.command(observation())
        self.c._scan_run = rig.run
        self.c._dispatch_scan(rig.run, intent, self.window, observation())
        self.assertEqual(self.c.input.backend.scrolls, [(275, 825, rig.run.TOP_JUMP_NOTCHES)])
        self.assertEqual(rig.run.phase, Phase.SCROLL)
        self.assertFalse(rig.run.top_jump_pending)

    def test_pause_cancels_undispatched_top_jump(self):
        rig = Rig()
        intent = rig.command(observation())
        self.c._scan_run = rig.run
        self.c.pause()
        with self.assertRaises(AutoSafetyError):
            self.c._dispatch_scan(rig.run, intent, self.window, observation())
        self.assertEqual(self.c.input.backend.scrolls, [])

    def test_fresh_destructive_capture_and_ack(self):
        rig, intent, analysis = self.prepare()
        self.c._dispatch_scan(rig.run, intent, self.window, analysis)
        self.c.capture.grab_window.assert_called_once_with(self.window.rect)
        self.assertEqual(len(self.c.input.backend.clicks), 1)
        self.assertEqual(rig.run.phase, Phase.CONFIRM)

    def test_each_of_five_columns_dispatches_exact_center_without_jitter(self):
        for column in range(5):
            rig = Rig()
            rig.run.seeking_top = False
            rig.scan = inventory(*(2 if i == column else 5 for i in range(5)))
            intent = rig.command(observation())
            self.c._scan_run = rig.run
            self.c.detector.analyze.return_value = observation()
            with patch('imprint_decompose.auto_controller.read_inventory', return_value=rig.scan):
                self.c._dispatch_scan(rig.run, intent, self.window, observation())
            self.assertEqual(self.c.input.backend.clicks[-1], (88+93*column, 724))
            self.assertEqual(rig.run.phase, Phase.OPEN)

    def test_candidate_changed_to_five_before_click_is_rejected(self):
        rig = Rig()
        rig.run.seeking_top = False
        intent = rig.command(observation())
        self.c._scan_run = rig.run
        self.c.detector.analyze.return_value = observation()
        with patch('imprint_decompose.auto_controller.read_inventory', return_value=inventory(5)):
            with self.assertRaises(AutoSafetyError):
                self.c._dispatch_scan(rig.run, intent, self.window, observation())
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_second_capture_new_red_match_blocks_dismantle(self):
        rig, intent, analysis = self.prepare()
        self.c.detector.analyze.return_value = observation(ImprintState.DETAIL, rig.run.item.current, (27,))
        with self.assertRaises(AutoSafetyError):
            self.c._dispatch_scan(rig.run, intent, self.window, analysis)
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_second_capture_wrong_dialog_blocks_confirmation(self):
        rig, intent, analysis = self.prepare('confirm')
        self.c.detector.analyze.return_value = observation()
        with self.assertRaises(AutoSafetyError):
            self.c._dispatch_scan(rig.run, intent, self.window, analysis)
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_pause_during_final_capture_prevents_native_input(self):
        rig, intent, analysis = self.prepare('confirm')
        def capture(_):
            self.c.pause()
            return self.frame
        self.c.capture.grab_window.side_effect = capture
        with self.assertRaises((BotError, AutoSafetyError)):
            self.c._dispatch_scan(rig.run, intent, self.window, analysis)
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_rapid_stop_manual_restart_cannot_borrow_new_enabled_event(self):
        rig, intent, analysis = self.prepare('confirm')
        def capture(_):
            self.c.stop()
            self.c.set_auto_mode(False)
            self.c.start()
            return self.frame
        self.c.capture.grab_window.side_effect = capture
        with self.assertRaises(AutoSafetyError):
            self.c._dispatch_scan(rig.run, intent, self.window, analysis)
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_geometry_and_focus_loss_invalidate_run(self):
        for change in ('geometry', 'focus'):
            with self.subTest(change=change):
                self.c.stop()
                self.c.start()
                self.c._scan_window = (self.window.hwnd, self.window.rect)
                self.c.locator.find.return_value = self.window
                self.c.locator.is_foreground.return_value = True
                if change == 'geometry':
                    self.c.locator.find.return_value = WindowInfo(123, 'test', Rect(1, 0, 551, 1020))
                else:
                    self.c.locator.is_foreground.return_value = False
                with self.assertRaises(BotError):
                    self.c._tick()
                self.assertFalse(self.c.enabled.is_set())
                self.assertFalse(self.c._scan_run.valid)
        self.assertEqual(self.c.input.backend.clicks, [])
        self.assertEqual(self.c.input.backend.scrolls, [])

    def test_changed_settings_stop_and_revoke_old_permission(self):
        rig, intent, _ = self.prepare('confirm')
        self.c.set_enhancement_settings(True, 1, 27)
        self.assertFalse(self.c.enabled.is_set())
        self.assertFalse(rig.run.allows(intent))
        self.c.start()
        self.assertEqual(self.c._scan_run.settings.rounds, 1)
        self.assertEqual(self.c._scan_run.settings.threshold, 27)
        self.assertIsNone(self.c._scan_run.item)

    def test_repeat_start_does_not_reset_active_session(self):
        before = self.c._scan_run
        self.c.start()
        self.assertIs(self.c._scan_run, before)

    def test_manual_original_five_is_protected_even_after_false_two_reading(self):
        self.c.stop()
        self.c.set_auto_mode(False)
        self.c._manual_origin_filled = 2
        self.c._manual_enhancement_sent = False
        with self.assertRaises(BotError):
            self.c._handle_detail(self.window, self.frame, observation(ImprintState.DETAIL,
                                  ('风暴', '烈焰', '电弧', '暗影', '大地')))
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_unrelated_manual_confirmation_cannot_be_adopted(self):
        self.c.stop()
        self.c.set_auto_mode(False)
        with self.assertRaises(BotError):
            self.c._handle_wait_confirm(self.window, observation(ImprintState.CONFIRM))
        self.assertEqual(self.c.input.backend.clicks, [])

    def test_debug_frames_use_real_save_signature_and_only_one_latest_file(self):
        self.c.debug_mode.set()
        self.c.detector.analyze.return_value = observation()
        with tempfile.TemporaryDirectory() as folder, \
             patch('tower_bot.config.debug_dir', return_value=Path(folder)), \
             patch('imprint_decompose.controller.draw_debug_overlay', return_value=self.frame), \
             patch('imprint_decompose.auto_controller.read_inventory', return_value=inventory(5)):
            for _ in range(3):
                self.c._tick()
            self.assertTrue(self.c.enabled.is_set())
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['imprint_latest.png'])
            self.assertEqual(cv2.imread(str(Path(folder) / 'imprint_latest.png')).shape, self.frame.shape)

    def test_visible_viewport_is_frozen_until_restart_and_used_for_selection_recheck(self):
        self.c.detector.analyze.return_value = observation()
        roi = (20, 680, 530, 948)
        with patch('imprint_decompose.auto_controller.inventory_viewport', return_value=roi) as viewport, \
             patch('imprint_decompose.auto_controller.read_inventory', return_value=inventory(2)) as reader:
            self.c._tick()
            self.c._tick()
            viewport.assert_called_once()
            self.assertEqual(reader.call_args.args[1], roi)
            rig = Rig()
            rig.run.seeking_top = False
            intent = rig.command(observation())
            self.c._scan_run = rig.run
            self.c._dispatch_scan(rig.run, intent, self.window, observation())
            self.assertEqual(reader.call_args.args[1], roi)
        self.c.stop()
        self.c.start()
        self.assertIsNone(self.c._scan_roi)

    def test_safety_stop_really_writes_debug_image_and_revokes_inputs(self):
        self.c.detector.analyze.return_value = observation()
        self.c._scan_run.step = Mock(side_effect=AutoSafetyError('test safety stop'))
        with tempfile.TemporaryDirectory() as folder, \
             patch('tower_bot.config.debug_dir', return_value=Path(folder)), \
             patch('imprint_decompose.controller.draw_debug_overlay', return_value=self.frame), \
             patch('imprint_decompose.auto_controller.read_inventory', return_value=inventory(5)):
            with self.assertRaisesRegex(BotError, 'test safety stop'):
                self.c._tick()
            image = Path(folder) / 'imprint_auto_safety_stop_latest.png'
            self.assertTrue(image.is_file())
            self.assertEqual(cv2.imread(str(image)).shape, self.frame.shape)
            raw = Path(folder) / 'imprint_auto_failure_raw_latest.png'
            np.testing.assert_array_equal(cv2.imread(str(raw)), self.frame)
            metadata = json.loads((Path(folder) / 'imprint_auto_failure_latest.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['phase'], 'LIST')
            self.assertEqual(metadata['scan']['cards'][0]['column'], 0)
        self.assertFalse(self.c.enabled.is_set())
        self.assertFalse(self.c._scan_run.valid)
        self.assertEqual(self.c.input.backend.clicks, [])
        self.assertEqual(self.c.input.backend.scrolls, [])

    def test_debug_failure_does_not_mask_original_safety_stop(self):
        self.c.detector.analyze.return_value = observation()
        self.c._scan_run.step = Mock(side_effect=AutoSafetyError('test safety stop'))
        with patch.object(self.c, '_save_debug', autospec=True, side_effect=OSError('test disk failure')), \
             patch('imprint_decompose.auto_controller.save_scan_failure', side_effect=OSError('raw disk failure')), \
             patch('imprint_decompose.auto_controller.read_inventory', return_value=inventory(5)), \
             self.assertLogs('imprint_decompose.auto_controller', level='WARNING') as logs:
            with self.assertRaisesRegex(BotError, 'test safety stop'):
                self.c._tick()
        self.assertIn('test disk failure', '\n'.join(logs.output))
        self.assertFalse(self.c.enabled.is_set())
        self.assertFalse(self.c._scan_run.valid)

    def test_manual_enhancement_timeout_uses_real_debug_signature(self):
        self.c.stop()
        self.c.set_auto_mode(False)
        self.c._enhancement_current_combination = ('风暴', '烈焰')
        self.c._enhancement_completed = 1
        self.c._phase_since = time.monotonic() - self.c.feature_cfg['actions']['enhancement_timeout'] - 1
        analysis = observation(ImprintState.DETAIL)
        with patch.object(self.c, '_save_debug', autospec=True) as save:
            with self.assertRaisesRegex(BotError, '强化后只识别到 2/3'):
                self.c._handle_detail(self.window, self.frame, analysis)
        save.assert_called_once_with(self.frame, analysis, tag='enhancement_slots_not_updated')


if __name__ == '__main__':
    unittest.main()
