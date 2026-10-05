import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from imprint_decompose.controller_v2 import _slot_alignment_quality, install_detail_slot_alignment
from imprint_decompose.config import load_feature_config
from imprint_decompose.models import DetailAnalysis, SlotReading


class DetailRecognitionTests(unittest.TestCase):
    def test_valid_alignment_is_rechecked_without_periodic_full_search(self):
        _, feature = load_feature_config()
        vision = feature['vision']
        valid_offset = [0]

        def read(_hsv, xs):
            base = vision['detail_slot_x_left'][0] if xs[0] < 200 else vision['detail_slot_x_right'][0]
            valid = xs[0]-base == valid_offset[0]
            slots = tuple(SlotReading(i, valid, '暗影' if valid else None,
                                      .65 if valid else 0., 135., 1.) for i in range(5))
            return slots, 355

        reader = Mock(side_effect=read)
        detector = SimpleNamespace(_read_slots=reader, _parchment_ratio=Mock(return_value=.8),
                                   _red_ratio=Mock(return_value=.2))
        install_detail_slot_alignment(detector, feature)
        frame = np.zeros((1020, 550, 3), np.uint8)

        def read_pair():
            detector._read_slots(frame, vision['detail_slot_x_left'])
            return detector._read_slots(frame, vision['detail_slot_x_right'])

        with patch('imprint_decompose.controller_v2.time.monotonic', return_value=1.):
            read_pair()
        self.assertEqual(reader.call_count, 50)  # initial 25-offset search
        reader.reset_mock()
        with patch('imprint_decompose.controller_v2.time.monotonic', return_value=100.):
            read_pair()
        self.assertEqual(reader.call_count, 2)  # new frame validation, no timer scan
        reader.reset_mock()
        valid_offset[0] = 8
        slots, _ = read_pair()
        self.assertEqual(reader.call_count, 52)  # invalid offset still recalibrates
        self.assertEqual(detector._detail_slot_alignment_state['offset'], 8)
        self.assertTrue(all(slot.active for slot in slots))

    def test_current_label_preserves_game_slot_order(self):
        detail = DetailAnalysis(
            left_slots=(),
            right_slots=(),
            left_star_score=0.0,
            right_star_score=0.0,
            left_panel_score=0.0,
            right_panel_score=0.0,
            right_combination=('暗影', '烈焰'),
            right_filled_count=2,
            right_ready=True,
        )
        self.assertEqual(detail.right_label, '暗影 + 烈焰')

    def test_real_row_beats_weak_decorative_row(self):
        weak = tuple(
            SlotReading(index=i, active=True, element='烈焰',
                        active_ratio=ratio, hue=0.0, confidence=0.5)
            for i, ratio in enumerate((0.26, 0.33, 0.35))
        )
        real = (
            SlotReading(0, True, '暗影', 0.64, 136.0, 1.0),
            SlotReading(1, True, '烈焰', 0.70, 3.0, 1.0),
        )
        self.assertGreater(
            _slot_alignment_quality(real, 361, 355),
            _slot_alignment_quality(weak, 295, 355),
        )


if __name__ == '__main__':
    unittest.main()
