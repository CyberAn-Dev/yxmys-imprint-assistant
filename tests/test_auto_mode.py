"""Offline state-machine regressions: these tests cannot send game input."""
import unittest
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
        rig.frame[150:250, 345:465] = 255
        with self.assertRaises(AutoSafetyError):
            rig.command(observation(State.DETAIL))

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


class ScrollWorkflowTests(unittest.TestCase):
    def test_top_confirmation_uses_four_wheels_not_eight(self):
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
        self.assertEqual(wheels, [1, 1, -1, 1])
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
        self.assertEqual(command.wheel, -1)

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
        wheels = []
        for _ in range(1200):
            rig.frame[680:970, 20:530] = texture[offset:offset+290]
            command = rig.step(observation())
            if command:
                self.assertEqual(command.kind, 'scroll')
                wheels.append(command.wheel)
                offset = max(0, min(560, offset-command.wheel*70))
                rig.ack(command)
            if rig.run.phase == Phase.DONE:
                break
        self.assertEqual(rig.run.phase, Phase.DONE)
        self.assertEqual(rig.run.pass_number, 2)
        self.assertEqual(offset, 560)
        self.assertIn(1, wheels)
        self.assertIn(-1, wheels)

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
