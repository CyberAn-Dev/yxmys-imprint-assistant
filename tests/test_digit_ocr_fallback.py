"""Numeric OCR regressions without gameplay captures or native input."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from imprint_decompose.digit_ocr_fallback import RedPercentageOCR, install_digit_ocr_fallback


class DigitOCRTests(unittest.TestCase):
    def test_padding_is_zero_in_normalized_space(self):
        blob = RedPercentageOCR._preprocess(np.full((24, 40), 255, np.uint8))
        self.assertEqual(blob.shape, (1, 3, 48, 320))
        self.assertEqual(blob.dtype, np.float32)
        self.assertTrue((blob[:, :, :, :80] == 1).all())
        self.assertTrue((blob[:, :, :, 80:] == 0).all())

    def test_percent_parser_never_accepts_a_numeric_suffix_or_missing_decimal(self):
        for text in ('108.9%', '8.9.1%', '.9%', '8.99%', '8.9% 28.9%', '100.1%', '-8.9%', '8.9', '?'):
            with self.subTest(text=text):
                self.assertIsNone(RedPercentageOCR._percentage(text, .99))
        for text, value in (('+8.9%', 8.9), ('18,9%', 18.9), ('+28.9%', 28.9), ('100%', 100)):
            self.assertEqual(RedPercentageOCR._percentage(text, .90)[0], value)
        self.assertIsNone(RedPercentageOCR._percentage('8.9%', .69))

    def test_two_consistent_views_are_required_and_conflicts_protect(self):
        for predictions, expected, calls in (
            ([('8.9%', .9), ('8,9%', .85)], 8.9, 2),
            ([('noise', .9), ('8.9%', .85), ('8.9%', .9)], 8.9, 3),
            ([('8.9%', .9), ('28.9%', .85)], None, 2),
            ([('8.9%', .9), ('noise', .9), ('noise', .9)], None, 3),
            ([('8.9%', .69)]*3, None, 3),
        ):
            engine = RedPercentageOCR({})
            engine._infer_text = Mock(side_effect=predictions)
            result, _ = engine._read_views(np.zeros((24, 180), np.uint8))
            self.assertEqual(result[0] if result else None, expected)
            self.assertEqual(engine._infer_text.call_count, calls)

    def test_failed_fallback_never_reauthorizes_low_confidence_baseline(self):
        detector = SimpleNamespace(cfg={}, analyze=Mock(), _read_red_percentage=Mock(return_value=(8.9, '8.9%', .5)))
        with patch.object(RedPercentageOCR, 'recognize', return_value=None):
            self.assertTrue(install_digit_ocr_fallback(detector, {}))
            self.assertEqual(detector._read_red_percentage(np.zeros((20, 80)), 0, 20)[0], None)

    def test_clear_template_stays_on_fast_path(self):
        detector = SimpleNamespace(cfg={}, analyze=Mock(), _read_red_percentage=Mock(return_value=(20., '20%', .95)))
        with patch.object(RedPercentageOCR, 'recognize') as fallback:
            self.assertTrue(install_digit_ocr_fallback(detector, {}))
            self.assertEqual(detector._read_red_percentage(np.zeros((20, 80)), 0, 20)[0], 20.)
            fallback.assert_not_called()

    def test_real_model_reads_decimal_values_on_portable_synthetic_rows(self):
        engine = RedPercentageOCR({})
        for value in ('8.9', '18.9', '20.0', '28.9'):
            row = np.full((36, 215, 3), (145, 164, 186), np.uint8)
            cv2.putText(row, '+'+value+'%', (62, 25), cv2.FONT_HERSHEY_SIMPLEX,
                        .65, (45, 45, 150), 1, cv2.LINE_AA)
            result, evidence = engine._read_views(cv2.cvtColor(row, cv2.COLOR_BGR2GRAY))
            self.assertIsNotNone(result, (value, evidence))
            self.assertAlmostEqual(result[0], float(value))


if __name__ == '__main__':
    unittest.main()
