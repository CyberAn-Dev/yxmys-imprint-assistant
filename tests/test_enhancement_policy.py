import unittest
from types import SimpleNamespace

from imprint_decompose.controller_v2 import (
    ImprintDecomposeController,
    enhancement_keep_reason,
    enhancement_slot_plan,
)


class EnhancementSlotPlanTests(unittest.TestCase):
    def test_target_one(self):
        self.assertEqual(enhancement_slot_plan(2, 1), (0, 1, False))
        self.assertEqual(enhancement_slot_plan(3, 1), (1, 0, False))
        self.assertEqual(enhancement_slot_plan(4, 1), (2, 0, True))
        self.assertEqual(enhancement_slot_plan(5, 1), (3, 0, True))

    def test_target_two(self):
        self.assertEqual(enhancement_slot_plan(2, 2), (0, 2, False))
        self.assertEqual(enhancement_slot_plan(3, 2), (1, 1, False))
        self.assertEqual(enhancement_slot_plan(4, 2), (2, 0, False))
        self.assertEqual(enhancement_slot_plan(5, 2), (3, 0, True))

    def test_target_three(self):
        self.assertEqual(enhancement_slot_plan(2, 3), (0, 3, False))
        self.assertEqual(enhancement_slot_plan(3, 3), (1, 2, False))
        self.assertEqual(enhancement_slot_plan(4, 3), (2, 1, False))
        self.assertEqual(enhancement_slot_plan(5, 3), (3, 0, False))

    def test_keep_rules_are_target_independent(self):
        originals = {'storm', 'arc'}
        self.assertIsNone(
            enhancement_keep_reason((12.0,), 0, 20.0, originals,
                                    {'storm', 'arc', 'earth'})
        )
        self.assertIn(
            '20', enhancement_keep_reason((20.0,), 0, 20.0, originals,
                                          {'storm', 'arc', 'earth'})
        )
        self.assertIn(
            '第三种', enhancement_keep_reason((), 0, 20.0, originals,
                                               {'storm', 'arc'})
        )

    def test_red_threshold_match_is_counted_once_per_detail_session(self):
        controller = object.__new__(ImprintDecomposeController)
        controller._red_threshold_counted = False
        controller.stats = SimpleNamespace(red_threshold_matches=0)
        controller._red_attribute_meets_threshold = lambda _analysis: True
        emitted = []
        controller._emit_stats = lambda: emitted.append(controller.stats.red_threshold_matches)

        controller._count_red_threshold_match(object())
        controller._count_red_threshold_match(object())

        self.assertEqual(controller.stats.red_threshold_matches, 1)
        self.assertEqual(emitted, [1])


if __name__ == '__main__':
    unittest.main()
