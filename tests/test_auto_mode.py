"""Offline state-machine regressions: these tests cannot send game input."""
import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from imprint_decompose.auto_mode import AutoRun, AutoSettings, AutoSafetyError, Phase
from imprint_decompose.auto_vision import ListScan, ScanCard
from imprint_decompose.controller_v2 import enhancement_keep_reason
from imprint_decompose.models import ImprintState as State, SlotReading

COLORS = ('风暴', '烈焰', '电弧', '暗影', '大地')
ROI = (20, 680, 530, 970)


def observation(state=State.LIST, elements=COLORS[:2], values=(), unreadable=0):
    slots = tuple(SlotReading(i, i < len(elements), elements[i] if i < len(elements) else None,
                              .8 if i < len(elements) else 0, 100, 1) for i in range(5))
    detail = SimpleNamespace(right_ready=True, right_slots=slots, right_combination=elements,
                             right_filled_count=len(elements)) if state == State.DETAIL else None
    return SimpleNamespace(state=state, detail=detail, dismantle_ready=state == State.DETAIL,
                           confirm_ready=state == State.CONFIRM, reward_ready=state == State.REWARD,
                           red_attribute_values=values, red_attribute_unreadable_count=unreadable)


def inventory(*counts):
    return ListScan(tuple(ScanCard(88+93*i, 724, 768, 15, COLORS[:n], True, i)
                          for i, n in enumerate(counts)), ROI)


class Rig:
    def __init__(self, rounds=1, confirmation='auto', **limits):
        self.now = 0
        self.run = AutoRun(AutoSettings(rounds, 20, confirmation, **limits), enhancement_keep_reason, 0)
        self.frame = np.zeros((1020, 550, 3), np.uint8)
        self.scan = inventory(2, 5, 4, 3, 5)
        self.actions = []

    def step(self, obs, scan=None):
        self.now += .25
        return self.run.step(obs, self.frame, scan or self.scan, self.now)

    def command(self, obs):
        for _ in range(40):
            command = self.step(obs)
            if command:
                return command
        raise AssertionError('No command')

    def ack(self, command):
        self.actions.append(command.kind)
        self.run.acknowledge(command, self.now)

    def open(self, initial=COLORS[:2]):
        self.run.seeking_top = False
        self.ack(self.command(observation()))
        for _ in range(3):
            self.step(observation(State.DETAIL, initial))
        assert self.run.phase == Phase.DETAIL

    def enhance(self, initial=COLORS[:2], result=None, values=()):
        elements = initial
        self.open(initial)
        for index in range(self.run.settings.rounds):
            command = self.command(observation(State.DETAIL, elements, values))
            assert command.kind == 'enhance'
            self.ack(command)
            self.assertions_before_observation(index)
            elements = (result or COLORS)[0:3+index]
            for _ in range(3):
                self.step(observation(State.DETAIL, elements, values))
            assert self.run.item.completed == index+1
        return elements

    def assertions_before_observation(self, index):
        assert self.run.item.completed == index
        assert self.run.clicks == index+1


class AutomaticWorkflowTests(unittest.TestCase):
    def test_exact_one_two_three_observed_enhancements_then_decompose(self):
        for target in (1, 2, 3):
            with self.subTest(target=target):
                rig = Rig(target)
                result = rig.enhance()
                command = rig.command(observation(State.DETAIL, result))
                self.assertEqual(command.kind, 'dismantle')
                self.assertTrue(rig.run.allows(command))
                rig.ack(command)
                confirm = rig.command(observation(State.CONFIRM))
                self.assertEqual(confirm.kind, 'confirm')
                rig.ack(confirm)
                reward = rig.command(observation(State.REWARD))
                self.assertEqual(reward.kind, 'reward')
                rig.ack(reward)
                for _ in range(3):
                    rig.step(observation(), inventory(5, 4, 3))
                self.assertEqual(rig.run.decomposed, 1)
                self.assertEqual(rig.run.processed, 1)
                self.assertIsNone(rig.run.item)
                self.assertEqual(rig.actions.count('enhance'), target)
                self.assertEqual(rig.actions.count('confirm'), 1)

    def test_original_three_four_five_never_enter_detail(self):
        for count in (3, 4, 5):
            rig = Rig(3)
            rig.run.seeking_top = False
            rig.scan = inventory(count)
            command = rig.command(observation())
            self.assertEqual(command.kind, 'scroll')
            self.assertIsNone(rig.run.item)

    def test_original_five_on_detail_recheck_is_blocked(self):
        for count in (3, 4, 5):
            rig = Rig(3)
            with self.assertRaises(AutoSafetyError):
                rig.open(COLORS[:count])
            self.assertEqual(rig.actions, ['select'])
            self.assertFalse(rig.run.valid)

    def test_initial_pair_must_match_in_physical_order(self):
        rig = Rig()
        with self.assertRaises(AutoSafetyError):
            rig.open(COLORS[1::-1])
        self.assertEqual(rig.actions, ['select'])

    def test_red_match_does_not_shorten_requested_rounds(self):
        rig = Rig(3)
        result = rig.enhance(values=(27,))
        keep = rig.command(observation(State.DETAIL, result, (27,)))
        self.assertEqual(keep.kind, 'keep')
        rig.ack(keep)
        for _ in range(3):
            rig.step(observation(), inventory(5))
        self.assertEqual(rig.run.kept, 1)
        self.assertEqual(rig.run.red_matches, 1)
        self.assertEqual(rig.actions.count('enhance'), 3)

    def test_existing_color_policy_is_reused(self):
        rig = Rig()
        result = rig.enhance(result=(COLORS[0], COLORS[1], COLORS[1]))
        self.assertEqual(rig.command(observation(State.DETAIL, result)).kind, 'keep')

    def test_low_red_cannot_be_rescued_by_color_rule(self):
        rig = Rig()
        result = rig.enhance(result=(COLORS[0], COLORS[1], COLORS[1]))
        self.assertEqual(rig.command(observation(State.DETAIL, result, (12,))).kind, 'dismantle')

    def test_unknown_red_halts_without_dismantle(self):
        rig = Rig()
        result = rig.enhance()
        with self.assertRaises(AutoSafetyError):
            rig.command(observation(State.DETAIL, result, (), 1))
        self.assertNotIn('dismantle', rig.actions)

    def test_bad_numeric_ocr_is_protected(self):
        for value in (float('inf'), -2, 150):
            rig = Rig()
            result = rig.enhance()
            with self.assertRaises(AutoSafetyError):
                rig.command(observation(State.DETAIL, result, (value,)))

    def test_material_failure_does_not_repeat_enhancement(self):
        rig = Rig()
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        with self.assertRaises(AutoSafetyError):
            for _ in range(40):
                self.assertIsNone(rig.step(observation(State.DETAIL)))
        self.assertEqual(rig.actions.count('enhance'), 1)

    def test_unexpected_growth_or_prefix_change_halts(self):
        for elements in (COLORS[:4], (COLORS[0], COLORS[2], COLORS[1])):
            rig = Rig()
            rig.open()
            rig.ack(rig.command(observation(State.DETAIL)))
            with self.assertRaises(AutoSafetyError):
                for _ in range(3):
                    rig.step(observation(State.DETAIL, elements))

    def test_identity_change_halts(self):
        rig = Rig()
        rig.open()
        x0, y0, x1, y1 = rig.run.identity_roi
        rig.frame[y0:y1, x0:x1] = 255
        with self.assertRaises(AutoSafetyError):
            rig.command(observation(State.DETAIL))

    def test_proficiency_toast_and_level_badge_do_not_change_imprint_identity(self):
        for slot_y in (332, 355):
            with self.subTest(slot_y=slot_y):
                rig = Rig(2)
                rig.run.seeking_top = False
                rig.ack(rig.command(observation()))
                initial = observation(State.DETAIL)
                initial.detail.right_slot_y = slot_y
                for _ in range(3):
                    rig.step(initial)
                self.assertEqual(rig.run.identity_roi, (310, slot_y-94, 490, slot_y-54))
                rig.ack(rig.command(initial))
                rig.frame[130:208, 50:500] = 220  # proficiency popup
                rig.frame[slot_y-119:slot_y-97, 435:465] = 180  # level 0 -> 10
                result = observation(State.DETAIL, COLORS[:3])
                result.detail.right_slot_y = slot_y
                for _ in range(3):
                    rig.step(result)
                self.assertEqual(rig.run.item.completed, 1)
                self.assertEqual(rig.run.identity_difference, 0)
                self.assertEqual(rig.command(result).kind, 'enhance')

    def test_verified_slot_growth_reuses_stability_for_next_enhancement(self):
        rig = Rig(2)
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        sent_at = rig.now
        result = observation(State.DETAIL, COLORS[:3])
        for elapsed in (.01, .10, .20, .40, .60):
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+elapsed))
            self.assertEqual(rig.run.item.completed, 0)
        command = rig.run.step(result, rig.frame, None, sent_at+.66)
        self.assertEqual(rig.run.item.completed, 1)
        self.assertEqual(command.kind, 'enhance')
        self.assertTrue(rig.run.allows(command))

    def test_final_filter_waits_for_animation_then_reuses_settled_reading(self):
        rig = Rig(1)
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        sent_at = rig.now
        result = observation(State.DETAIL, COLORS[:3])
        for elapsed in (.01, .10, .20, .25, .35, .60):
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+elapsed))
        command = rig.run.step(result, rig.frame, None, sent_at+.66)
        self.assertEqual(command.kind, 'dismantle')

    def test_new_slot_transient_color_is_not_committed(self):
        rig = Rig(2)
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        sent_at = rig.now
        transient = observation(State.DETAIL, COLORS[:2] + ('大地',))
        for elapsed in (.01, .12, .23, .40):
            self.assertIsNone(rig.run.step(transient, rig.frame, None, sent_at+elapsed))
            self.assertEqual(rig.run.item.completed, 0)
        result = observation(State.DETAIL, COLORS[:3])
        for elapsed in (.45, .56, .70):
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+elapsed))
        command = rig.run.step(result, rig.frame, None, sent_at+.81)
        self.assertEqual(rig.run.item.current, COLORS[:3])
        self.assertEqual(rig.run.item.completed, 1)
        self.assertEqual(command.kind, 'enhance')

    def test_same_classified_color_but_animated_pixels_cannot_confirm_growth(self):
        rig = Rig()
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        sent_at = rig.now
        result = observation(State.DETAIL, COLORS[:3])
        for i in range(12):
            rig.frame[341:370, 300:505] = i*15
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+.1*(i+1)))
            self.assertEqual(rig.run.item.completed, 0)
        for elapsed in (1.3, 1.4):
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+elapsed))
        self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+1.59))
        self.assertEqual(rig.run.step(result, rig.frame, None, sent_at+1.61).kind, 'dismantle')
        self.assertEqual(rig.run.item.completed, 1)
        self.assertEqual(rig.actions.count('enhance'), 1)

    def test_intermediate_red_animation_does_not_delay_settled_slots(self):
        rig = Rig(2)
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        sent_at = rig.now
        for i, elapsed in enumerate((.01, .12, .24, .40, .60)):
            result = observation(State.DETAIL, COLORS[:3], (8.9,) if i % 2 else (), i % 2)
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+elapsed))
        command = rig.run.step(observation(State.DETAIL, COLORS[:3], (), 1), rig.frame, None, sent_at+.66)
        self.assertEqual(command.kind, 'enhance')
        self.assertEqual(rig.run.item.completed, 1)
        self.assertFalse(rig.run.item.approved)

    def test_final_red_animation_still_resets_decision_stability(self):
        rig = Rig(1)
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        sent_at = rig.now
        for i in range(12):
            result = observation(State.DETAIL, COLORS[:3], (8.9,) if i % 2 else (28.9,))
            self.assertIsNone(rig.run.step(result, rig.frame, None, sent_at+.1*(i+1)))
        self.assertFalse(rig.run.item.approved)

    def test_settled_record_change_still_fails_and_keeps_diagnostic_record(self):
        rig = Rig()
        result = rig.enhance()
        with self.assertRaisesRegex(AutoSafetyError, '预期=.*实际='):
            rig.command(observation(State.DETAIL, result[:2] + ('大地',)))
        self.assertIsNone(rig.run.item)
        self.assertEqual(rig.run.session_evidence['expected'], result)
        self.assertEqual(rig.run.last_elements, result[:2] + ('大地',))

    def test_color_switch_off_decomposes_nonred_two_color_result(self):
        rig = Rig(keep_two_elements=False)
        result = rig.enhance(result=(COLORS[0], COLORS[1], COLORS[1]))
        self.assertEqual(rig.command(observation(State.DETAIL, result)).kind, 'dismantle')

    def test_color_switch_off_still_preserves_threshold_match_and_unknown_red(self):
        for unreadable, values in ((0, (20,)), (1, ())):
            rig = Rig(keep_two_elements=False)
            result = rig.enhance(result=(COLORS[0], COLORS[1], COLORS[1]))
            if unreadable:
                with self.assertRaises(AutoSafetyError):
                    rig.command(observation(State.DETAIL, result, values, unreadable))
            else:
                self.assertEqual(rig.command(observation(State.DETAIL, result, values)).kind, 'keep')

    def test_imprint_name_change_after_enhancement_remains_protected(self):
        rig = Rig(2)
        rig.open()
        rig.ack(rig.command(observation(State.DETAIL)))
        x0, y0, x1, y1 = rig.run.identity_roi
        rig.frame[y0:y1, x0:x1] = 255
        with self.assertRaisesRegex(AutoSafetyError, '刻印名称发生变化'):
            rig.command(observation(State.DETAIL, COLORS[:3]))
        self.assertEqual(rig.actions.count('enhance'), 1)
        self.assertNotIn('dismantle', rig.actions)

    def test_both_destructive_gates_require_initial_two_record(self):
        for gate in ('dismantle', 'confirm'):
            rig = Rig(3)
            result = rig.enhance()
            command = rig.command(observation(State.DETAIL, result))
            if gate == 'confirm':
                rig.ack(command)
                command = rig.command(observation(State.CONFIRM))
            rig.run.item.origin_filled = 5
            self.assertFalse(rig.run.allows(command))
            with self.assertRaises(AutoSafetyError):
                rig.ack(command)

    def test_pause_invalidates_already_prepared_confirmation(self):
        rig = Rig()
        result = rig.enhance()
        rig.ack(rig.command(observation(State.DETAIL, result)))
        command = rig.command(observation(State.CONFIRM))
        rig.run.invalidate()
        self.assertFalse(rig.run.allows(command))
        self.assertIsNone(rig.step(observation(State.CONFIRM)))

    def test_restart_does_not_adopt_existing_confirmation(self):
        rig = Rig()
        with self.assertRaises(AutoSafetyError):
            rig.step(observation(State.CONFIRM))
        self.assertFalse(rig.run.valid)

    def test_manual_confirmation_never_sends_confirm(self):
        rig = Rig(confirmation='manual')
        result = rig.enhance()
        rig.ack(rig.command(observation(State.DETAIL, result)))
        for _ in range(12):
            self.assertIsNone(rig.step(observation(State.CONFIRM)))
        self.assertNotIn('confirm', rig.actions)
        rig.step(observation(State.REWARD))
        reward = rig.command(observation(State.REWARD))
        rig.ack(reward)
        for _ in range(3):
            rig.step(observation(), inventory(5))
        self.assertEqual(rig.run.decomposed, 1)

    def test_manual_cancel_halts(self):
        rig = Rig(confirmation='manual')
        result = rig.enhance()
        rig.ack(rig.command(observation(State.DETAIL, result)))
        for _ in range(3):
            rig.step(observation(State.CONFIRM))
        with self.assertRaises(AutoSafetyError):
            rig.step(observation(State.DETAIL, result))

    def test_confirmation_is_never_retried(self):
        rig = Rig()
        result = rig.enhance()
        rig.ack(rig.command(observation(State.DETAIL, result)))
        rig.ack(rig.command(observation(State.CONFIRM)))
        with self.assertRaises(AutoSafetyError):
            for _ in range(40):
                self.assertIsNone(rig.step(observation(State.CONFIRM)))
        self.assertEqual(rig.actions.count('confirm'), 1)

    def test_duplicate_colors_do_not_suppress_next_card(self):
        rig = Rig()
        result = rig.enhance(result=(COLORS[0], COLORS[1], COLORS[1]))
        rig.ack(rig.command(observation(State.DETAIL, result)))
        for _ in range(3):
            rig.step(observation())
        command = rig.command(observation())
        self.assertEqual(command.kind, 'select')
        self.assertEqual(rig.run.item.serial, 2)

    def test_row_then_column_order_tolerates_one_pixel_jitter(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = ListScan((ScanCard(182, 724, 767, 15, COLORS[:2], True, 1),
                            ScanCard(88, 724, 768, 15, COLORS[:2], True, 0)), ROI)
        self.assertEqual(rig.command(observation()).point[0], 88)

    def test_unacknowledged_stale_intent_is_not_reused(self):
        rig = Rig()
        rig.run.seeking_top = False
        command = rig.command(observation())
        with self.assertRaises(AutoSafetyError):
            rig.step(observation(State.DETAIL))
        self.assertFalse(rig.run.allows(command))

    def test_limits_pause_instead_of_claiming_completion(self):
        rig = Rig(max_seconds=.1)
        with self.assertRaises(AutoSafetyError):
            rig.step(observation())
        self.assertNotEqual(rig.run.phase, Phase.DONE)
        rig = Rig(max_items=1)
        rig.run.seeking_top = False
        rig.run.processed = 1
        with self.assertRaises(AutoSafetyError):
            rig.command(observation())

    def test_unrecognized_list_is_not_reported_as_empty(self):
        rig = Rig()
        rig.scan = ListScan((), ROI)
        with self.assertRaises(AutoSafetyError):
            rig.command(observation())
        self.assertEqual(rig.actions, [])

    def test_detail_loss_revokes_authorization(self):
        rig = Rig()
        rig.open()
        with self.assertRaises(AutoSafetyError):
            rig.step(observation(State.UNKNOWN))
        self.assertIsNone(rig.run.item)

    def test_short_inventory_last_deletion_completes_only_with_empty_evidence(self):
        rig = Rig()
        rig.run.single_page = True
        rig.run.short_page_items = 1
        result = rig.enhance()
        rig.ack(rig.command(observation(State.DETAIL, result)))
        rig.ack(rig.command(observation(State.CONFIRM)))
        rig.ack(rig.command(observation(State.REWARD)))
        self.assertTrue(rig.run.expects_empty())
        for _ in range(3):
            rig.step(observation(), ListScan((), ROI, empty=True))
        self.assertEqual(rig.run.phase, Phase.DONE)
        self.assertEqual(rig.run.decomposed, 1)

    def test_short_inventory_empty_claim_needs_exact_removal_count(self):
        rig = Rig()
        rig.run.single_page = True
        rig.run.short_page_items = 2
        result = rig.enhance()
        rig.ack(rig.command(observation(State.DETAIL, result)))
        rig.ack(rig.command(observation(State.CONFIRM)))
        self.assertFalse(rig.run.expects_empty())

    def test_clipped_two_at_verified_top_prevents_silent_skip(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.run.check_top_crop = True
        rig.scan = ListScan((ScanCard(88, 650, 694, 15, COLORS[:2], False, 0),), ROI)
        with self.assertRaises(AutoSafetyError):
            rig.command(observation())

    def test_clipped_bottom_cannot_be_reported_as_completed(self):
        rig = Rig()
        rig.run.seeking_top = False
        scan = ListScan(inventory(5).cards, ROI, clipped_bottom=True)
        with self.assertRaises(AutoSafetyError):
            rig.run._boundary(1, scan)
        self.assertNotEqual(rig.run.phase, Phase.DONE)

    def test_verified_bottom_with_candidate_processes_it_before_claiming_done(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.run._boundary(0, rig.scan)
        self.assertEqual(rig.run.phase, Phase.LIST)
        self.assertEqual(rig.command(observation()).kind, 'select')

    def test_inventory_mutation_clears_scroll_response_evidence(self):
        rig = Rig()
        result = rig.enhance(result=(COLORS[0], COLORS[1], COLORS[1]))
        rig.ack(rig.command(observation(State.DETAIL, result)))
        rig.run.scroll_leg_moved = True
        rig.run.scroll_end_clamped = True
        for _ in range(3):
            rig.step(observation(), inventory(5))
        self.assertFalse(rig.run.scroll_leg_moved)
        self.assertFalse(rig.run.scroll_end_clamped)


class ScrollWorkflowTests(unittest.TestCase):
    def test_edge_clamped_short_step_does_not_inflate_next_pass_wheel(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = inventory(5)
        texture = np.random.default_rng(14).integers(0, 256, (800, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[70:360]
        command = rig.command(observation())
        self.assertEqual(command.wheel, -2)
        rig.ack(command)
        rig.frame[680:970, 20:530] = texture[105:395]
        command = rig.command(observation())
        self.assertEqual(command.wheel, -2)
        self.assertEqual(rig.run.scroll_pixels_per_notch[-1], 70)

    def test_larger_viewport_allows_two_row_target(self):
        rig = Rig()
        rig.frame = np.zeros((1200, 550, 3), np.uint8)
        rig.run.seeking_top = False
        rig.scan = ListScan(inventory(5).cards, (20, 680, 530, 1080))
        texture = np.random.default_rng(13).integers(0, 256, (2000, 510, 3), dtype=np.uint8)
        offset, wheels = 0, []
        for _ in range(7):
            rig.frame[680:1080, 20:530] = texture[offset:offset+400]
            command = rig.command(observation())
            wheels.append(abs(command.wheel))
            offset -= command.wheel*6
            rig.ack(command)
        self.assertEqual(wheels, [1, 2, 4, 8, 16, 30, 30])

    def test_slow_real_game_wheel_adapts_towards_two_rows_without_losing_overlap(self):
        for seeking_top in (False, True):
            with self.subTest(seeking_top=seeking_top):
                rig = Rig()
                rig.run.seeking_top = seeking_top
                rig.scan = inventory(5)
                texture = np.random.default_rng(11).integers(0, 256, (2000, 510, 3), dtype=np.uint8)
                offset, wheels = 1000 if seeking_top else 0, []
                for _ in range(6):
                    rig.frame[680:970, 20:530] = texture[offset:offset+290]
                    command = rig.command(observation())
                    self.assertEqual(command.kind, 'scroll')
                    self.assertTrue(rig.run.allows(command))
                    wheels.append(abs(command.wheel))
                    offset -= command.wheel * 6
                    rig.ack(command)
                self.assertEqual(wheels, [2, 4, 8, 15, 15, 15] if seeking_top else [1, 2, 4, 8, 16, 28])
                self.assertTrue(all(wheel * 6 <= (90 if seeking_top else 168) for wheel in wheels))
                self.assertTrue(all(b <= 2*a for a, b in zip(wheels, wheels[1:])))

    def test_adapted_downward_step_still_cannot_skip_unobserved_rows(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.run.down_notches = 8
        rig.scan = inventory(5)
        texture = np.random.default_rng(12).integers(0, 256, (1000, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        command = rig.command(observation())
        self.assertEqual(command.wheel, -8)
        rig.ack(command)
        rig.frame[680:970, 20:530] = texture[500:790]
        with self.assertRaisesRegex(AutoSafetyError, '无法验证滚动方向和重叠区域'):
            rig.command(observation())

    def test_downward_step_guard_rejects_stale_or_excessive_calibration(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = inventory(5)
        command = rig.command(observation())
        rig.run.down_notches = 2
        self.assertFalse(rig.run.allows(command))
        rig.run.down_notches = rig.run.MAX_SCROLL_NOTCHES+1
        rig.run.pending = replace(command, wheel=-rig.run.down_notches)
        self.assertFalse(rig.run.allows(rig.run.pending))

    def test_two_notch_ascent_repeats_only_after_observed_stable_movement(self):
        rig = Rig()
        rig.scan = inventory(2)
        texture = np.random.default_rng(8).integers(0, 256, (900, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[420:710]
        command = rig.command(observation())
        self.assertEqual(command.wheel, 2)
        self.assertEqual(rig.run.scroll_kind, 'top_seek')
        rig.ack(command)
        dispatched_at = rig.now
        rig.frame[680:970, 20:530] = texture[280:570]
        for elapsed in (.06, .17, .28):
            self.assertIsNone(rig.run.step(observation(), rig.frame, rig.scan, dispatched_at+elapsed))
        next_step = rig.run.step(observation(), rig.frame, rig.scan, dispatched_at+.31)
        self.assertEqual(next_step.kind, 'scroll')
        self.assertEqual(next_step.wheel, 2)
        self.assertEqual(rig.run.last_motion['displacement'], 140)
        self.assertTrue(rig.run.seeking_top)
        self.assertIsNone(rig.run.item)

    def test_excessive_two_notch_distance_reduces_to_one_without_claiming_top(self):
        rig = Rig()
        texture = np.random.default_rng(8).integers(0, 256, (1000, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[560:850]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[210:500]
        self.assertIsNone(rig.step(observation()))
        self.assertIsNone(rig.step(observation()))
        verification = rig.step(observation())
        self.assertEqual(verification.kind, 'scroll')
        self.assertEqual(verification.wheel, 1)
        self.assertTrue(rig.run.seeking_top)
        self.assertFalse(rig.run.boundary_proven)
        self.assertIsNone(rig.run.item)
        rig.ack(verification)
        rig.frame[680:970, 20:530] = texture[140:430]
        next_step = rig.command(observation())
        self.assertEqual(next_step.wheel, 1)
        self.assertEqual(rig.run.last_motion['displacement'], 70)

    def test_two_notch_ascent_stops_only_at_top_even_if_game_clamps_each_event(self):
        for notch_pixels in (35, 70):
            with self.subTest(notch_pixels=notch_pixels):
                rig = Rig()
                rig.scan = inventory(2)
                texture = np.random.default_rng(9).integers(0, 256, (900, 510, 3), dtype=np.uint8)
                offset, wheels, selected = 420, [], False
                for _ in range(120):
                    rig.frame[680:970, 20:530] = texture[offset:offset+290]
                    command = rig.step(observation())
                    if command:
                        if command.kind == 'select':
                            self.assertEqual(offset, 0)
                            selected = True
                            break
                        wheels.append(command.wheel)
                        offset = max(0, min(560, offset-command.wheel*notch_pixels))
                        rig.ack(command)
                self.assertTrue(selected)
                self.assertEqual(wheels[-4:], [2, 2, -1, 1])
                self.assertTrue(all(wheel == 2 for wheel in wheels[:-4]))
                self.assertEqual(rig.run.last_boundary['method'], 'probe_restore')
                self.assertEqual(rig.run.last_boundary['stationary_attempts'], 2)

    def test_game_that_caps_wheel_event_to_one_notch_still_reaches_top(self):
        rig = Rig()
        rig.scan = inventory(2)
        texture = np.random.default_rng(9).integers(0, 256, (900, 510, 3), dtype=np.uint8)
        offset, wheels, selected = 420, [], False
        for _ in range(120):
            rig.frame[680:970, 20:530] = texture[offset:offset+290]
            command = rig.step(observation())
            if command:
                if command.kind == 'select':
                    self.assertEqual(offset, 0)
                    selected = True
                    break
                wheels.append(command.wheel)
                # Emulate a game that handles even two notches as one notch.
                offset = max(0, min(560, offset-(70 if command.wheel > 0 else -70)))
                rig.ack(command)
        self.assertTrue(selected)
        self.assertEqual(wheels, [2]*8 + [-1, 1])

    def test_top_seek_guard_requires_upward_reposition_no_item_and_at_most_two_notches(self):
        rig = Rig()
        command = rig.command(observation())
        self.assertTrue(rig.run.allows(command))
        rig.run.seeking_top = False
        self.assertFalse(rig.run.allows(command))
        rig.run.seeking_top = True
        rig.run.top_seek_notches = 1
        self.assertFalse(rig.run.allows(command))
        rig.run.top_seek_notches = 2
        rig.run.item = object()
        self.assertFalse(rig.run.allows(command))
        rig.run.item = None
        for invalid_wheel in (-2, rig.run.MAX_SCROLL_NOTCHES+1, 240):
            rig.run.top_seek_notches = invalid_wheel
            rig.run.pending = replace(command, wheel=invalid_wheel)
            self.assertFalse(rig.run.allows(rig.run.pending))
        rig.run.invalidate()
        self.assertFalse(rig.run.allows(command))

    def test_wrong_direction_while_seeking_top_is_not_accepted(self):
        rig = Rig()
        texture = np.random.default_rng(8).integers(0, 256, (900, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[210:500]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[350:640]
        with self.assertRaisesRegex(AutoSafetyError, '无法验证滚动方向'):
            for _ in range(4):
                rig.step(observation())
        self.assertNotIn('select', rig.actions)

    def test_one_notch_fallback_cannot_keep_accepting_unknown_motion(self):
        rig = Rig()
        texture = np.random.default_rng(8).integers(0, 256, (1500, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[1000:1290]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[600:890]
        reduced = rig.command(observation())
        self.assertEqual(reduced.wheel, 1)
        rig.ack(reduced)
        rig.frame[680:970, 20:530] = texture[200:490]
        with self.assertRaisesRegex(AutoSafetyError, '无法验证滚动方向'):
            for _ in range(4):
                rig.step(observation())
        self.assertNotIn('select', rig.actions)

    def test_one_ignored_upward_event_does_not_count_as_top(self):
        rig = Rig()
        texture = np.random.default_rng(8).integers(0, 256, (900, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[420:710]
        rig.ack(rig.command(observation()))
        retry = rig.command(observation())
        self.assertEqual(retry.wheel, 2)
        self.assertEqual(rig.run.still, 1)
        rig.ack(retry)
        rig.frame[680:970, 20:530] = texture[280:570]
        self.assertEqual(rig.command(observation()).wheel, 2)
        self.assertEqual(rig.run.still, 0)
        self.assertTrue(rig.run.seeking_top)
        self.assertIsNone(rig.run.item)

    def test_unresponsive_scroll_does_not_claim_top(self):
        rig = Rig()
        with self.assertRaisesRegex(AutoSafetyError, '正反滚轮都未产生可验证移动'):
            for _ in range(80):
                command = rig.step(observation())
                if command:
                    rig.ack(command)
        self.assertNotIn('select', rig.actions)
        self.assertEqual(rig.run.scrolls, 3)

    def test_downward_page_skip_remains_forbidden(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = inventory(5)
        texture = np.random.default_rng(5).integers(0, 256, (1000, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        command = rig.command(observation())
        self.assertEqual(command.wheel, -1)
        rig.ack(command)
        rig.frame[680:970, 20:530] = texture[500:790]
        with self.assertRaisesRegex(AutoSafetyError, '无法验证滚动方向和重叠区域'):
            for _ in range(4):
                rig.step(observation())

    def test_downward_scroll_wait_reduced_but_still_requires_three_stable_frames(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = inventory(5)
        texture = np.random.default_rng(7).integers(0, 256, (600, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        rig.ack(rig.command(observation()))
        dispatched_at = rig.now
        rig.frame[680:970, 20:530] = texture[70:360]
        for elapsed in (.06, .17, .28):
            self.assertIsNone(rig.run.step(observation(), rig.frame, rig.scan, dispatched_at+elapsed))
        command = rig.run.step(observation(), rig.frame, rig.scan, dispatched_at+.31)
        self.assertIsNotNone(command)
        self.assertEqual(command.wheel, -2)

    def test_already_at_top_stops_after_two_unchanged_steps_and_boundary_check(self):
        rig = Rig()
        rig.scan = inventory(2)
        texture = np.random.default_rng(8).integers(0, 256, (600, 510, 3), dtype=np.uint8)
        offset, wheels, selected = 0, [], None
        for _ in range(60):
            rig.frame[680:970, 20:530] = texture[offset:offset+290]
            command = rig.step(observation())
            if command:
                if command.kind == 'select':
                    selected = command
                    break
                wheels.append(command.wheel)
                offset = max(0, min(280, offset-command.wheel*70))
                rig.ack(command)
        self.assertIsNotNone(selected)
        self.assertEqual(wheels, [2, 2, -1, 1])
        self.assertEqual(offset, 0)
        self.assertLessEqual(rig.now, 4)

    def test_scroll_reuses_stable_frame_without_second_list_wait(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = inventory(5)
        texture = np.random.default_rng(8).integers(0, 256, (600, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[70:360]
        self.assertIsNone(rig.step(observation()))
        self.assertIsNone(rig.step(observation()))
        command = rig.step(observation())
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, 'scroll')
        self.assertEqual(command.wheel, -2)

    def test_coordinate_jitter_does_not_restart_slot_stability(self):
        rig = Rig()
        rig.run.seeking_top = False
        command = None
        for index in range(4):
            d = index % 2
            scan = ListScan((ScanCard(88+d, 724+d, 768+d, 15, COLORS[:2], True, 0),
                             ScanCard(181, 724, 769-d, 15, COLORS[:2], True, 1)), ROI)
            command = rig.step(observation(), scan)
            if command:
                break
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, 'select')
        self.assertLess(command.point[0], 100)

    def test_changed_attributes_do_require_new_stable_readings(self):
        rig = Rig()
        rig.run.seeking_top = False
        for index in range(20):
            self.assertIsNone(rig.step(observation(), inventory(2 if index % 2 else 3)))
        self.assertEqual(rig.actions, [])

    def test_scroll_motion_can_finish_but_uncertain_slots_cannot_authorize_more_input(self):
        rig = Rig()
        rig.run.seeking_top = False
        rig.scan = inventory(5)
        texture = np.random.default_rng(8).integers(0, 256, (600, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[70:360]
        uncertain = ListScan(rig.scan.cards, ROI, uncertain=True, issues=('灰槽无法确认',))
        for _ in range(4):
            self.assertIsNone(rig.step(observation(), uncertain))
        self.assertEqual(rig.run.phase, Phase.LIST)
        self.assertEqual(rig.run.last_motion['displacement'], -70)
        with self.assertRaisesRegex(AutoSafetyError, '列表槽位.*灰槽无法确认') as error:
            for _ in range(40):
                rig.step(observation(), uncertain)
        self.assertNotIn('材料不足', str(error.exception))
        self.assertEqual(rig.run.scrolls, 1)

    def test_scroll_animation_never_stable_times_out_without_material_message(self):
        rig = Rig()
        rig.scan = inventory(5)
        rig.ack(rig.command(observation()))
        with self.assertRaisesRegex(AutoSafetyError, '滚动后画面未停稳') as error:
            for index in range(40):
                rig.frame[680:970, 20:530] = (index % 2)*255
                self.assertIsNone(rig.step(observation()))
        self.assertNotIn('材料不足', str(error.exception))

    def test_positively_identified_short_page_can_run_without_scroll_motion(self):
        rig = Rig()
        rig.scan = ListScan(inventory(2).cards, ROI, short_page=True)
        selected = False
        for _ in range(60):
            command = rig.step(observation())
            if command:
                if command.kind == 'select':
                    selected = True
                    break
                rig.ack(command)
        self.assertTrue(selected)
        self.assertTrue(rig.run.single_page)
        self.assertEqual(rig.run.short_page_items, 1)

    def test_verified_top_bottom_and_full_rescan_after_changes(self):
        rig = Rig()
        rig.scan = inventory(5, 4, 3, 5, 4)
        rig.run.pass_processed = 1  # a changed inventory requires another sweep
        texture = np.random.default_rng(7).integers(0, 256, (850, 510, 3), dtype=np.uint8)
        offset = 210
        wheels, first_ascent_steps = [], {}
        for _ in range(1200):
            rig.frame[680:970, 20:530] = texture[offset:offset+290]
            command = rig.step(observation())
            if command:
                self.assertEqual(command.kind, 'scroll')
                wheels.append(command.wheel)
                if rig.run.seeking_top:
                    first_ascent_steps.setdefault(rig.run.pass_number, command.wheel)
                offset = max(0, min(560, offset-command.wheel*70))
                rig.ack(command)
            if rig.run.phase == Phase.DONE:
                break
        self.assertEqual(rig.run.phase, Phase.DONE)
        self.assertEqual(rig.run.pass_number, 2)
        self.assertEqual(offset, 560)
        self.assertIn(2, wheels)
        self.assertIn(-1, wheels)
        self.assertEqual(first_ascent_steps, {1: 2, 2: 2})
        self.assertTrue(all(abs(wheel) <= 2 for wheel in wheels))

    def test_confirmed_motion_plus_one_ignored_event_does_not_claim_boundary(self):
        rig = Rig()
        texture = np.random.default_rng(46).integers(0, 256, (900, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[420:710]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[280:570]
        rig.ack(rig.command(observation()))
        self.assertTrue(rig.run.scroll_leg_moved)
        retry = rig.command(observation())  # one ignored wheel, not two
        self.assertEqual(rig.run.still, 1)
        self.assertTrue(rig.run.seeking_top)
        rig.ack(retry)
        rig.frame[680:970, 20:530] = texture[140:430]
        self.assertEqual(rig.command(observation()).kind, 'scroll')
        self.assertEqual(rig.run.still, 0)
        self.assertTrue(rig.run.seeking_top)

    def test_small_nonempty_inventory_finishes_without_redundant_boundary_roundtrips(self):
        def replay(force_probe):
            rig = Rig()
            rig.scan = inventory(5, 4, 5, 4, 5)
            texture = np.random.default_rng(45).integers(0, 256, (350, 510, 3), dtype=np.uint8)
            offset, wheels = 31, []
            for _ in range(120):
                rig.frame[680:970, 20:530] = texture[offset:offset+290]
                if force_probe:
                    rig.run.scroll_leg_moved = False  # old always-probe path
                command = rig.step(observation())
                if command:
                    self.assertEqual(command.kind, 'scroll')
                    wheels.append(command.wheel)
                    offset = max(0, min(31, offset-command.wheel*7))
                    rig.ack(command)
                if rig.run.phase == Phase.DONE:
                    self.assertEqual(offset, 31)
                    return wheels, rig.now
            self.fail('inventory did not finish')
        old_wheels, old_time = replay(True)
        new_wheels, new_time = replay(False)
        self.assertEqual((len(old_wheels), len(new_wheels)), (13, 9))
        self.assertLess(new_time, old_time)

    def test_wheel_stops_responding_after_normal_motion_is_not_a_boundary(self):
        rig = Rig()
        rig.scan = inventory(5)
        rig.run.seeking_top = False
        texture = np.random.default_rng(47).integers(0, 256, (900, 510, 3), dtype=np.uint8)
        rig.frame[680:970, 20:530] = texture[:290]
        rig.ack(rig.command(observation()))
        rig.frame[680:970, 20:530] = texture[70:360]
        rig.ack(rig.command(observation()))
        self.assertTrue(rig.run.scroll_leg_moved)
        self.assertFalse(rig.run.scroll_end_clamped)
        with self.assertRaisesRegex(AutoSafetyError, '正反滚轮都未产生可验证移动'):
            for _ in range(30):
                command = rig.step(observation())
                if command:
                    rig.ack(command)
        self.assertNotEqual(rig.run.phase, Phase.DONE)

    def test_unresponsive_scroll_does_not_claim_bottom(self):
        rig = Rig()
        rig.scan = inventory(5)
        rig.run.seeking_top = False
        with self.assertRaises(AutoSafetyError):
            for _ in range(80):
                command = rig.step(observation())
                if command:
                    rig.ack(command)
        self.assertNotEqual(rig.run.phase, Phase.DONE)
        self.assertEqual(rig.run.scrolls, 3)


if __name__ == '__main__':
    unittest.main()
