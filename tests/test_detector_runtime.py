"""No game I/O: verify dispatch parity and avoid non-detail OCR entirely."""
from dataclasses import asdict
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from imprint_decompose.detector import ImprintDetector
from imprint_decompose.detector_runtime import install_state_first_analysis
from imprint_decompose.digit_ocr_fallback import install_digit_ocr_fallback
from imprint_decompose.config import load_feature_config
from imprint_decompose.models import ImprintState


class StateFirstDetectorTests(unittest.TestCase):
    def detector(self, *, reward=False, confirm=False, detail=False, count=5, score=.8):
        return SimpleNamespace(
            _reward_signal=Mock(return_value=(.2, .3, .4, reward)),
            _confirm_signal=Mock(return_value=(.5, .6, .7, confirm)),
            _detail_signal=Mock(return_value=(SimpleNamespace(right_ready=True), .9, detail, .8, detail)),
            _list_signal=Mock(return_value=(score, count, ())),
            _red_attribute_signal=Mock(return_value=(.7, 3, (15., 27.), ('15%', '27%'), 1)))

    def test_non_detail_never_reads_red_or_leaks_old_ocr_values(self):
        for flags, state in (({}, ImprintState.LIST), ({'count': 0}, ImprintState.UNKNOWN),
                             ({'confirm': True, 'detail': True}, ImprintState.CONFIRM),
                             ({'reward': True, 'confirm': True, 'detail': True}, ImprintState.REWARD)):
            d = self.detector(**flags)
            install_state_first_analysis(d)
            a = d.analyze(np.zeros((10, 10, 3), np.uint8))
            self.assertEqual(a.state, state)
            d._red_attribute_signal.assert_not_called()
            self.assertEqual((a.red_attribute_values, a.red_attribute_unreadable_count), ((), 0))

    def test_detail_ocr_and_unreadable_protection_are_unchanged(self):
        d = self.detector(detail=True)
        frame = np.zeros((10, 10, 3), np.uint8)
        old = ImprintDetector.analyze(d, frame)
        install_state_first_analysis(d)
        new = d.analyze(frame)
        before, after = asdict(old), asdict(new)
        before.pop('reason'); after.pop('reason')
        self.assertEqual(before, after)
        self.assertEqual(d._red_attribute_signal.call_count, 2)
        self.assertEqual(new.red_attribute_unreadable_count, 1)

    def test_list_confidence_threshold_remains_fail_closed(self):
        d = self.detector(score=.049)
        install_state_first_analysis(d)
        self.assertEqual(d.analyze(np.zeros((10, 10, 3), np.uint8)).state, ImprintState.UNKNOWN)

    def test_wrapper_still_supplies_current_bgr_frame_for_detail_ocr(self):
        d = self.detector(detail=True)
        d._read_red_percentage = Mock(return_value=(None, '', 0.))
        install_state_first_analysis(d)
        _, feature = load_feature_config()
        install_digit_ocr_fallback(d, feature)
        for value in (0, 80):
            frame = np.full((10, 10, 3), value, np.uint8)
            d.analyze(frame)
            self.assertIs(d._digit_ocr_frame, frame)


if __name__ == '__main__':
    unittest.main()
