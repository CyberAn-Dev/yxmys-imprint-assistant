"""Read-only capacity reporting; never authorizes game actions."""
import math
import re

from .digit_ocr_fallback import RedPercentageOCR


def parse_count(text, confidence):
    matches = list(re.finditer(r'(?<![\d/])(\d{1,5})\s*/\s*(\d{1,5})(?![\d/])', text))
    if len(matches) != 1 or not math.isfinite(confidence) or confidence < .78:
        return None
    count, capacity = map(int, matches[0].groups())
    return (count, capacity) if 0 <= count <= capacity and capacity > 0 else None


class InventoryCounter:
    def __init__(self):
        self.ocr = RedPercentageOCR({})

    def read(self, frame, roi):
        # The game frame uses the same normalized 550px coordinates as the
        # detector. Three overlapping crops tolerate the supported title-bar
        # offsets. Require agreement; conflicting confident reads stay unknown.
        if frame is None or frame.shape[1] != 550:
            return None
        readings = []
        for offset in (-25, -20, -15):
            top = int(roi[1])+offset
            if top < 0 or top+28 > frame.shape[0]:
                return None
            candidate = parse_count(*self.ocr._infer_text(frame[top:top+28, 145:272]))
            if candidate is not None:
                readings.append(candidate)
        return readings[0] if len(readings) >= 2 and len(set(readings)) == 1 else None


def format_count(value):
    return f'{value[0]}/{value[1]}' if value is not None else '未识别'
