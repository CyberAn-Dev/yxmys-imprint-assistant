import unittest
from pathlib import Path

import numpy as np

from imprint_decompose.config import load_feature_config
from imprint_decompose.controller_v2 import ImprintDecomposeController


class GameDigitTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = Path(__file__).resolve().parents[1]
        root_cfg, feature_cfg = load_feature_config(
            repo / 'src' / 'imprint_decompose' / 'default.yaml',
            root_config_path=repo / 'config' / 'default.yaml',
        )
        cls.controller = ImprintDecomposeController(root_cfg, feature_cfg, dry_run=True)
        cls.archive_path = repo / 'src' / 'imprint_decompose' / 'game_digit_templates.npz'

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def test_all_digits_are_installed(self):
        templates = self.controller.detector._digit_recognizer._templates
        self.assertEqual(set(templates), set(map(str, range(10))))
        self.assertTrue(all(len(templates[digit]) > 56 for digit in templates))

    def test_native_templates_classify_as_their_labels(self):
        recognizer = self.controller.detector._digit_recognizer
        with np.load(self.archive_path, allow_pickle=False) as archive:
            for digit in map(str, range(10)):
                for mask in archive[digit]:
                    actual, confidence = recognizer.classify(mask.astype(bool))
                    self.assertEqual(actual, digit)
                    self.assertGreaterEqual(confidence, 0.99)


if __name__ == '__main__':
    unittest.main()
