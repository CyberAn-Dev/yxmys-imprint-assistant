import unittest

from imprint_decompose.controller_v2 import _slot_alignment_quality
from imprint_decompose.models import DetailAnalysis, SlotReading


class DetailRecognitionTests(unittest.TestCase):
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
