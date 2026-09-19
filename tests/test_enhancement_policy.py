import unittest

from imprint_decompose.controller_v2 import (
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


if __name__ == '__main__':
    unittest.main()
