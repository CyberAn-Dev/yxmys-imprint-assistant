"""Startup preferences must never imply permission to send game input."""
import unittest
from unittest.mock import patch

from imprint_decompose.config import load_feature_config
from imprint_decompose.controller_v2 import ImprintDecomposeController


class StartupDefaultTests(unittest.TestCase):
    def setUp(self):
        self.worker = self.enterContext(patch.object(ImprintDecomposeController, '_ensure_worker'))
        self.c = ImprintDecomposeController(*load_feature_config(), dry_run=True)
        self.addCleanup(self.c.shutdown)

    def test_startup_defaults_are_auto_two_twenty_but_not_running(self):
        self.assertTrue(self.c.stats.auto_mode)
        self.assertTrue(self.c.stats.enhancement_enabled)
        self.assertEqual(self.c.stats.enhancement_target, 2)
        self.assertEqual(self.c.stats.red_attribute_threshold, 20.)
        self.assertEqual(self.c.stats.auto_phase, '待开始')
        self.assertTrue(self.c.stats.keep_two_elements)
        self.assertEqual(self.c.stats.auto_completion_id, 0)
        self.assertFalse(self.c.enabled.is_set())
        self.assertFalse(self.c._auto_enabled, 'legacy scanner stays disabled')
        self.assertIsNone(self.c._scan_run)
        self.worker.assert_not_called()
        self.assertEqual(self.c.input.backend.clicks, [])
        self.assertEqual(self.c.input.backend.scrolls, [])

    def test_explicit_start_uses_the_default_settings(self):
        self.c.start()
        self.assertTrue(self.c.enabled.is_set())
        self.assertEqual(self.c._scan_run.settings.rounds, 2)
        self.assertEqual(self.c._scan_run.settings.threshold, 20.)
        self.assertEqual(self.c.input.backend.clicks, [])
        self.assertEqual(self.c.input.backend.scrolls, [])

    def test_user_choices_are_not_reset_when_pausing_or_restarting(self):
        self.c.set_keep_two_elements(False)
        self.c.set_auto_mode(False)
        self.c.set_enhancement_settings(False, 1, 27)
        self.assertFalse(self.c.stats.auto_mode)
        self.assertFalse(self.c.stats.enhancement_enabled)
        self.assertFalse(self.c.enabled.is_set())
        self.c.set_auto_mode(True)
        self.c.start()
        self.c.pause()
        self.c.start()
        self.assertEqual(self.c._scan_run.settings.rounds, 1)
        self.assertEqual(self.c._scan_run.settings.threshold, 27.)
        self.assertFalse(self.c._scan_run.settings.keep_two_elements)


if __name__ == '__main__':
    unittest.main()
