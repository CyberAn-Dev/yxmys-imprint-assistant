import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from imprint_decompose.controller_v2 import (
    ImprintDecomposeController,
    enhancement_keep_reason,
    enhancement_slot_plan,
)


class EnhancementSlotPlanTests(unittest.TestCase):
    def test_color_switch_is_applied_by_manual_enhancement_workflow(self):
        from imprint_decompose.controller import ImprintDecomposeController as BaseController
        for enabled in (True, False):
            controller = object.__new__(ImprintDecomposeController)
            elements = ('storm', 'arc', 'arc', 'storm')
            controller._manual_origin_filled = 2
            controller._enhancement_is_enabled = lambda: True
            controller._enhancement_current_combination = elements
            controller._enhancement_original_elements = elements[:2]
            controller._enhancement_progress = lambda: (2, 2)
            controller._phase_since = time.monotonic()-1
            controller._initial_existing_enhancements = 0
            controller._result_signature = (2, elements, (), 0)
            controller._result_seen = 1
            controller.stats = SimpleNamespace(red_attribute_threshold=20, keep_two_elements=enabled)
            controller._keep_detail = Mock()
            controller._record_operation = Mock()
            analysis = SimpleNamespace(detail=SimpleNamespace(right_ready=True, right_combination=elements,
                                        right_filled_count=4), dismantle_ready=True,
                                        red_attribute_values=(), red_attribute_unreadable_count=0)
            with patch.object(BaseController, '_handle_detail') as legacy:
                controller._handle_detail(None, None, analysis)
                self.assertEqual(controller._keep_detail.called, enabled)
                self.assertEqual(legacy.called, not enabled)

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
        self.assertIsNone(
            enhancement_keep_reason((12.0,), 0, 20.0,
                                    ('storm', 'arc'), ('storm', 'arc'))
        )
        self.assertIn(
            '20', enhancement_keep_reason((20.0,), 0, 20.0, originals,
                                          {'storm', 'arc', 'earth'})
        )
        self.assertIn(
            '第三种', enhancement_keep_reason((), 0, 20.0, originals,
                                               {'storm', 'arc'})
        )

    def test_duplicate_original_elements_ignore_new_color(self):
        reason = enhancement_keep_reason(
            (), 0, 20.0,
            ('storm', 'storm'),
            ('storm', 'storm', 'arc', 'arc'),
        )
        self.assertIn('新增一种颜色', reason)

    def test_duplicate_original_elements_with_two_new_colors_are_not_kept(self):
        self.assertIsNone(
            enhancement_keep_reason(
                (), 0, 20.0,
                ('storm', 'storm'),
                ('storm', 'storm', 'arc', 'earth'),
            )
        )

    def test_below_threshold_red_value_cannot_use_color_rule(self):
        self.assertIsNone(
            enhancement_keep_reason(
                (12.0,), 0, 20.0,
                ('storm', 'arc'), ('storm', 'arc'),
            )
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
