import unittest
from unittest.mock import Mock

import numpy as np

from imprint_decompose.inventory_count import InventoryCounter, format_count, parse_count


class InventoryCountTests(unittest.TestCase):
    def test_valid_and_unknown_counts(self):
        self.assertEqual(parse_count(':836/1600', .9), (836, 1600))
        self.assertEqual(parse_count('0/1600', .9), (0, 1600))
        for text, score in [('1700/1600', .9), ('8361600', .9), ('0/0', .9),
                            ('836/1600 4/12', .9), ('836/1600', .5), ('836/1600', float('nan'))]:
            self.assertIsNone(parse_count(text, score))
        self.assertEqual(format_count(None), '未识别')

    def test_agreement_and_conflict(self):
        reader = InventoryCounter()
        reader.ocr = Mock()
        frame = np.zeros((1020, 550, 3), dtype=np.uint8)
        roi = (20, 680, 530, 970)
        reader.ocr._infer_text.side_effect = [('836/1600', .9), ('836/1600', .95), ('', 0)]
        self.assertEqual(reader.read(frame, roi), (836, 1600))
        reader.ocr._infer_text.side_effect = [('836/1600', .9), ('838/1600', .95), ('836/1600', .9)]
        self.assertIsNone(reader.read(frame, roi))
        reader.ocr._infer_text.side_effect = [('836/1600', .9), ('', 0), ('', 0)]
        self.assertIsNone(reader.read(frame, roi))
        self.assertIsNone(reader.read(None, roi))
