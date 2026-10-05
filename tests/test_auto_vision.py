"""User-provided cropped screenshots are fixtures, not click calibration."""
from pathlib import Path
import unittest

import cv2
import numpy as np

from imprint_decompose.auto_vision import (blank_inventory, inventory_viewport, list_chrome,
                                           read_inventory, scroll_displacement)

FIXTURES = Path(__file__).parent / 'fixtures'


class InventoryVisionTests(unittest.TestCase):
    def test_complete_slots_tight_to_bottom_override_artwork_height_guess(self):
        # Reproduce the live .34-pixel margin failure with existing public
        # fixtures, without adding the user's full gameplay/debug captures.
        frame = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        for bottom in (291, 292, 293):
            with self.subTest(bottom=bottom):
                scan = read_inventory(frame, (20, 60, 475, bottom))
                self.assertFalse(scan.uncertain, scan.issues)
                self.assertFalse(scan.clipped_bottom)
                self.assertEqual(len(scan.cards), 15)
                self.assertEqual(len(scan.candidates), 10)
                self.assertTrue(all(c.complete for c in scan.cards[-5:]))

    def test_truly_cut_bottom_slot_outline_is_still_protected(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        for bottom in (288, 289, 290):
            with self.subTest(bottom=bottom):
                scan = read_inventory(frame, (20, 60, 475, bottom))
                self.assertTrue(scan.clipped_bottom)
                self.assertFalse(any(c.slot_y > 270 for c in scan.candidates))

    def test_bottom_missing_grey_cannot_borrow_neighbor_valid_strip(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        baseline = read_inventory(frame, (20, 60, 475, 292))
        c = baseline.cards[-1]
        sx = round(c.x + 2*c.pitch)
        frame[c.slot_y-9:c.slot_y+10, sx-8:sx+9] = (145, 145, 145)
        scan = read_inventory(frame, (20, 60, 475, 292))
        self.assertTrue(scan.clipped_bottom or scan.uncertain)
        self.assertFalse(any(card.column == c.column and card.slot_y > 270 for card in scan.candidates))

    def test_bottom_navigation_is_excluded_but_dark_artwork_is_not(self):
        frame = np.full((1020, 550, 3), (110, 127, 141), np.uint8)
        roi = (20, 680, 530, 970)
        self.assertEqual(inventory_viewport(frame, roi), roi)
        frame[948:] = (55, 55, 55)
        self.assertEqual(inventory_viewport(frame, roi), (20, 680, 530, 948))
        frame[:] = (110, 127, 141)
        frame[948:, 200:300] = (55, 55, 55)
        self.assertEqual(inventory_viewport(frame, roi), roi)
        frame[:] = 0  # an entirely unknown black screen is not a nav edge
        self.assertEqual(inventory_viewport(frame, roi), roi)

    def test_hidden_bottom_slots_are_partial_cards_not_recognition_errors(self):
        source = cv2.imread(str(FIXTURES / 'inventory_five.png'))[125:357, 20:488]
        roi = (20, 680, 530, 970)
        for offset in (15, 20, 25):
            with self.subTest(offset=offset):
                frame = np.full((1020, 550, 3), (110, 127, 141), np.uint8)
                frame[680+offset:680+offset+len(source), 20:488] = source
                frame[948:] = (55, 55, 55)
                # Reproduce the old failure after a scroll: artwork is
                # visible but its slots are covered by fixed navigation.
                self.assertTrue(read_inventory(frame, roi).uncertain)
                scan = read_inventory(frame, inventory_viewport(frame, roi))
                self.assertFalse(scan.uncertain, scan.issues)
                self.assertTrue(scan.clipped_bottom)
                self.assertEqual(len(scan.cards), 10)
                self.assertEqual(scan.candidates, ())

    def test_artwork_fragments_are_not_extra_slots_at_scrolled_offsets(self):
        source = cv2.imread(str(FIXTURES / 'inventory_mixed.png'))[107:348, 20:488]
        for offset in (0, 50, 60, 61, 65, 70):
            with self.subTest(offset=offset):
                frame = np.full((1020, 550, 3), (110, 127, 141), np.uint8)
                height = min(len(source), 290-offset)
                frame[680+offset:680+offset+height, 20:488] = source[:height]
                scan = read_inventory(frame, (20, 680, 530, 970))
                self.assertFalse(scan.uncertain, scan.issues)
                self.assertEqual(len(scan.cards), 15)
                self.assertEqual([c.column for c in scan.candidates], [2, 3, 4])

    def test_missing_middle_columns_do_not_renumber_fifth_column(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        # Destroy two slot strips but retain their artwork as missing-card
        # evidence. The remaining gap is THREE columns, not one column.
        for sy in (105, 195, 284):
            frame[sy-9:sy+10, 208:365] = (110, 127, 141)
        scan = read_inventory(frame, (20, 60, 475, 298))
        right = [c for c in scan.cards if c.x > 375]
        self.assertTrue(right)
        self.assertTrue(all(c.column == 4 for c in right))
        self.assertTrue(scan.uncertain)

    def test_original_five_all_protected(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_five.png'))
        scan = read_inventory(frame, (20, 125, 488, 357))
        self.assertFalse(scan.uncertain)
        self.assertEqual(len(scan.cards), 10)
        self.assertTrue(all(c.filled == 5 for c in scan.cards))
        self.assertEqual(scan.candidates, ())
        self.assertTrue(scan.clipped_bottom)

    def test_mixed_shapes_and_counts(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_mixed.png'))
        scan = read_inventory(frame, (20, 107, 488, 348))
        self.assertFalse(scan.uncertain)
        self.assertEqual(len(scan.cards), 15)
        self.assertEqual(sorted(c.filled for c in scan.cards), [2]*3+[3]*2+[4]*10)
        self.assertEqual([c.column for c in scan.candidates], [2, 3, 4])

    def test_two_active_three_grey_and_clipped_top(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        scan = read_inventory(frame, (20, 60, 475, 298))
        self.assertFalse(scan.uncertain)
        self.assertEqual(len(scan.cards), 15)
        self.assertEqual(len(scan.candidates), 10)
        self.assertTrue(all(not c.complete for c in scan.cards[:5]))
        self.assertTrue(all(c.y > 60 and c.y < 298 for c in scan.candidates))
        self.assertEqual(set(c.column for c in scan.candidates), set(range(5)))
        self.assertFalse(scan.clipped_bottom)

    def test_reference_resizing_remains_conservative(self):
        source = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        scale = 550 / source.shape[1]
        frame = cv2.resize(source, (550, round(source.shape[0]*scale)))
        roi = tuple(round(v*scale) for v in (20, 60, 475, 298))
        scan = read_inventory(frame, roi)
        self.assertFalse(scan.uncertain)
        self.assertEqual(len(scan.candidates), 10)

    def test_grey_requires_positive_evidence(self):
        frame = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        # Remove the final grey slot of the first complete card. A missing
        # recognition is not equivalent to an empty attribute slot.
        frame[186:203, 86:104] = (145, 145, 145)
        scan = read_inventory(frame, (20, 60, 475, 298))
        self.assertTrue(scan.uncertain)
        self.assertFalse(any(c.x < 100 and 180 < c.slot_y < 210 for c in scan.candidates))

    def test_scroll_needs_real_overlap_and_correct_direction(self):
        texture = np.random.default_rng(3).integers(0, 256, (600, 510, 3), dtype=np.uint8)
        before = texture[0:290]
        after = texture[70:360]
        self.assertEqual(scroll_displacement(before, after, 190), -70)
        self.assertEqual(scroll_displacement(after, before, 190), 70)
        self.assertIsNone(scroll_displacement(before, texture[300:590], 190))

    def test_small_live_boundary_probe_is_visible_but_one_pixel_jitter_is_not(self):
        texture = np.random.default_rng(31).integers(0, 256, (600, 510, 3), dtype=np.uint8)
        before = texture[:270]
        for distance in (2, 3, 4, 5, 7):
            with self.subTest(distance=distance):
                self.assertEqual(scroll_displacement(before, texture[distance:distance+270], 170), -distance)
        self.assertIsNone(scroll_displacement(before, texture[1:271], 170))

    def test_identical_rows_are_not_unique_scroll_identity(self):
        row = np.random.default_rng(3).integers(0, 256, (90, 510, 3), dtype=np.uint8)
        frame = np.concatenate([row]*4)[:290]
        self.assertIsNone(scroll_displacement(frame, frame.copy(), 190))

    def test_fixed_toolbar_over_repeated_art_does_not_hide_unique_slot_motion(self):
        rng = np.random.default_rng(21)
        row = rng.integers(40, 200, (90, 510, 3), dtype=np.uint8)
        texture = np.concatenate([row]*8)
        header = rng.integers(0, 256, (24, 510, 3), dtype=np.uint8)
        for index in range(8):
            texture[index*90+60:index*90+66] = rng.integers(0, 256, (6, 510, 3), dtype=np.uint8)
        before, after = texture[:270].copy(), texture[82:352].copy()
        before[:24] = after[:24] = header
        self.assertEqual(scroll_displacement(before, after, 170), -82)
        self.assertEqual(scroll_displacement(after, before, 170), 82)

    def test_positive_short_page_and_empty_surface_evidence(self):
        source = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        scale = 550/477
        source = cv2.resize(source, (550, round(source.shape[0]*scale)))
        frame = np.full((1020, 550, 3), (110, 127, 141), np.uint8)
        roi = (20, 680, 530, 970)
        self.assertTrue(blank_inventory(frame, roi))
        frame[680:785] = source[135:240]
        scan = read_inventory(frame, roi)
        self.assertFalse(scan.uncertain)
        self.assertEqual(len(scan.cards), 5)
        self.assertTrue(scan.short_page)
        self.assertFalse(blank_inventory(frame, roi))
        self.assertGreater(list_chrome(frame, roi).size, 0)

    def test_two_row_short_inventory_with_fixed_toolbar_and_bottom_nav(self):
        source = cv2.imread(str(FIXTURES / 'inventory_two.png'))
        frame = np.full((1020, 550, 3), (110, 127, 141), np.uint8)
        for top in (727, 818):
            frame[top:top+80, :477] = source[135:215]
        frame[949:] = (55, 55, 55)
        roi = inventory_viewport(frame, (20, 680, 530, 970))
        scan = read_inventory(frame, roi)
        self.assertEqual(len(scan.cards), 10)
        self.assertFalse(scan.uncertain, scan.issues)
        self.assertTrue(all(c.complete for c in scan.cards))
        self.assertTrue(scan.short_page)
        # Any following artwork / coloured fragment blocks the empty-band
        # evidence. Stationary input alone must never complete the list.
        frame[922:940, 200:230] = (20, 20, 200)
        self.assertFalse(read_inventory(frame, roi).short_page)

    def test_click_centres_land_inside_artwork_not_slot_strip(self):
        for name, roi in (('inventory_mixed.png', (20, 107, 488, 348)),
                          ('inventory_two.png', (20, 60, 475, 298))):
            frame = cv2.imread(str(FIXTURES / name))
            scan = read_inventory(frame, roi)
            for c in scan.candidates:
                patch = cv2.cvtColor(frame[c.y-6:c.y+7, c.x-6:c.x+7], cv2.COLOR_BGR2HSV)
                red = ((patch[:, :, 0] < 13) | (patch[:, :, 0] > 170)) & (patch[:, :, 1] > 80)
                self.assertGreater(red.mean(), .35)
                self.assertGreater(c.slot_y-c.y, 25)


if __name__ == '__main__':
    unittest.main()
