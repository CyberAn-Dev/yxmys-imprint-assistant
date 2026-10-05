import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from imprint_decompose.config import load_feature_config
from imprint_decompose.controller_v2 import ImprintDecomposeController
from imprint_decompose.game_digit_templates import install_game_digit_templates


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
        # System-font templates are optional and depend on Windows' installed
        # fonts. Check the bundled native glyphs themselves, not a developer
        # machine's total count (56 font templates + native templates).
        with np.load(self.archive_path, allow_pickle=False) as archive:
            self.assertEqual(set(archive.files), set(map(str, range(10))))
            for digit in archive.files:
                native = archive[digit].astype(bool)
                self.assertGreater(len(native), 0, digit)
                self.assertEqual(native.shape[1:], (24, 24), digit)
                self.assertTrue(all(mask.any() for mask in native), digit)
                self.assertGreaterEqual(len(templates[digit]), len(native), digit)
                np.testing.assert_array_equal(templates[digit][:len(native)], native)
                self.assertTrue(all(mask.dtype == np.bool_ for mask in templates[digit][:len(native)]))

    def test_installation_does_not_depend_on_system_font_count(self):
        with np.load(self.archive_path, allow_pickle=False) as archive:
            for count in (0, 14, 28, 56):
                with self.subTest(system_templates=count):
                    baseline = {digit: [np.zeros((24, 24), dtype=bool) for _ in range(count)]
                                for digit in archive.files}
                    detector = SimpleNamespace(_digit_recognizer=SimpleNamespace(_templates=dict(baseline)))
                    installed = install_game_digit_templates(detector)
                    self.assertEqual(installed, sum(len(archive[digit]) for digit in archive.files))
                    for digit in archive.files:
                        native = archive[digit].astype(bool)
                        result = detector._digit_recognizer._templates[digit]
                        self.assertEqual(len(result), len(native)+count, digit)
                        np.testing.assert_array_equal(result[:len(native)], native)
                        for actual, original in zip(result[len(native):], baseline[digit]):
                            self.assertIs(actual, original)

    def test_native_templates_classify_without_any_system_fonts(self):
        recognizer_type = type(self.controller.detector._digit_recognizer)
        with patch.object(recognizer_type, '_load_system_font_templates'):
            recognizer = recognizer_type()
        install_game_digit_templates(SimpleNamespace(_digit_recognizer=recognizer))
        self.assert_native_classification(recognizer)

    def test_native_templates_classify_as_their_labels(self):
        recognizer = self.controller.detector._digit_recognizer
        self.assert_native_classification(recognizer)

    def assert_native_classification(self, recognizer):
        with np.load(self.archive_path, allow_pickle=False) as archive:
            for digit in map(str, range(10)):
                for mask in archive[digit]:
                    actual, confidence = recognizer.classify(mask.astype(bool))
                    self.assertEqual(actual, digit)
                    self.assertGreaterEqual(confidence, 0.99)


if __name__ == '__main__':
    unittest.main()
