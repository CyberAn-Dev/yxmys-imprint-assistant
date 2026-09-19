"""Install digit templates extracted from the game's red percentage text."""

import sys
from pathlib import Path

import numpy as np


def _template_path():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base / 'imprint_decompose' / 'game_digit_templates.npz'


def install_game_digit_templates(detector):
    """Prepend game-native templates to the detector's font templates."""
    path = _template_path()
    if not path.exists():
        return 0
    recognizer = getattr(detector, '_digit_recognizer', None)
    template_map = getattr(recognizer, '_templates', None)
    if not isinstance(template_map, dict):
        return 0

    installed = 0
    with np.load(path, allow_pickle=False) as archive:
        for digit in map(str, range(10)):
            if digit not in archive:
                continue
            native = [item.astype(bool) for item in archive[digit]]
            template_map[digit] = native + list(template_map.get(digit, ()))
            installed += len(native)
    return installed
