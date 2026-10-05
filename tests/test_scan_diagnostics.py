import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from imprint_decompose.scan_diagnostics import save_scan_failure
from test_auto_mode import Rig


class ScanDiagnosticsTests(unittest.TestCase):
    def test_stall_report_is_separate_bounded_and_versioned(self):
        from imprint_decompose import __version__
        rig = Rig()
        with tempfile.TemporaryDirectory() as directory, \
                patch('tower_bot.config.debug_dir', return_value=Path(directory)):
            for _ in range(3):
                save_scan_failure(rig.frame, None, rig.run, {}, category='stall')
            self.assertEqual(len(list(Path(directory).iterdir())), 2)
            metadata = json.loads((Path(directory) / 'imprint_auto_stall_latest.json').read_text(encoding='utf8'))
            self.assertEqual(metadata['category'], 'stall')
            self.assertEqual(metadata['version'], __version__)
            self.assertIn('captured_at', metadata)
            self.assertEqual(metadata['images']['raw'], 'imprint_auto_stall_raw_latest.png')

    def test_identity_reference_survives_invalidation_for_diagnostics(self):
        rig = Rig()
        rig.open()
        rig.run.invalidate()
        with tempfile.TemporaryDirectory() as directory, \
                patch('tower_bot.config.debug_dir', return_value=Path(directory)):
            save_scan_failure(rig.frame, None, rig.run, {})
            metadata = json.loads((Path(directory) / 'imprint_auto_failure_latest.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['identity_roi'], list(rig.run.identity_roi))
            self.assertEqual(set(metadata['images']), {'raw', 'identity_before', 'identity_after'})

    def test_repeated_reports_overwrite_bounded_raw_evidence(self):
        rig = Rig()
        rig.run.scroll_before = np.full((290, 510, 3), 10, np.uint8)
        with tempfile.TemporaryDirectory() as directory, \
                patch('tower_bot.config.debug_dir', return_value=Path(directory)):
            for value in (25, 30, 35):
                rig.frame[:] = value
                save_scan_failure(rig.frame, rig.scan, rig.run, {'analyze': 22.})
            self.assertEqual(len(list(Path(directory).iterdir())), 4)
            metadata = json.loads((Path(directory) / 'imprint_auto_failure_latest.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['timings_ms'], {'analyze': 22.})
            np.testing.assert_array_equal(cv2.imread(str(Path(directory) / metadata['images']['raw'])), rig.frame)
            np.testing.assert_array_equal(cv2.imread(str(Path(directory) / metadata['images']['before'])), rig.run.scroll_before)
            rig.run.scroll_before = None
            save_scan_failure(rig.frame, None, rig.run, {})
            metadata = json.loads((Path(directory) / 'imprint_auto_failure_latest.json').read_text(encoding='utf-8'))
            self.assertEqual(set(metadata['images']), {'raw'})


if __name__ == '__main__':
    unittest.main()
