"""User-provided cropped screenshots are fixtures, not click calibration."""
from pathlib import Path
import unittest

import cv2
import numpy as np

from imprint_decompose.auto_vision import blank_inventory, list_chrome, read_inventory, scroll_displacement

FIXTURES = Path(__file__).parent / 'fixtures'


class InventoryVisionTests(unittest.TestCase):
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

    def test_identical_rows_are_not_unique_scroll_identity(self):
        row = np.random.default_rng(3).integers(0, 256, (90, 510, 3), dtype=np.uint8)
        frame = np.concatenate([row]*4)[:290]
        self.assertIsNone(scroll_displacement(frame, frame.copy(), 190))

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
