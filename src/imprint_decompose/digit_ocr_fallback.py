"""Low-confidence red percentage OCR fallback.

The detector's template recognizer is deliberately kept as the fast path. This
module only runs the bundled PP-OCRv5 mobile recognition model when a digit
template result is missing or not sufficiently confident. The model is used
through OpenCV's DNN backend, so the packaged application does not need a
separate OCR runtime or an internet connection.
"""

from __future__ import annotations

import logging
import re
import sys
import time
import types
from pathlib import Path
from typing import Any, Mapping, Optional

import cv2
import numpy as np


LOGGER = logging.getLogger(__name__)
_PERCENT_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*%")
_MODEL_NAME = "en_PP-OCRv5_rec_mobile.onnx"
_DICT_NAME = "ppocrv5_en_dict.txt"


def _asset_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return root / "imprint_decompose" / "models" / name
    return Path(__file__).resolve().parent / "models" / name


class RedPercentageOCR:
    """Recognize one cropped red percentage line with a bundled ONNX model."""

    def __init__(self, feature_cfg: Mapping[str, Any]) -> None:
        self._feature_cfg = feature_cfg
        self._net: Optional[cv2.dnn_Net] = None
        self._characters: Optional[list[str]] = None
        self._cache: dict[tuple[Any, ...], tuple[float, Optional[tuple[float, str, float]]]] = {}
        self._last_failure_at = 0.0

    @property
    def available(self) -> bool:
        return _asset_path(_MODEL_NAME).exists() and _asset_path(_DICT_NAME).exists()

    def _ensure_loaded(self) -> bool:
        if self._net is not None and self._characters is not None:
            return True
        if not self.available:
            return False
        if time.monotonic() - self._last_failure_at < 5.0:
            return False
        try:
            self._net = cv2.dnn.readNetFromONNX(str(_asset_path(_MODEL_NAME)))
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            # PP-OCR CTC uses index 0 as blank and has a trailing space class.
            dictionary = _asset_path(_DICT_NAME).read_text(encoding="utf-8").splitlines()
            self._characters = [""] + dictionary + [" "]
            return True
        except Exception:
            self._last_failure_at = time.monotonic()
            self._net = None
            self._characters = None
            LOGGER.exception("无法加载红色数字 OCR 模型")
            return False

    @staticmethod
    def _preprocess(image: np.ndarray) -> np.ndarray:
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        image = np.asarray(image, dtype=np.uint8)
        if image.size == 0:
            raise ValueError("empty OCR crop")
        height, width = image.shape[:2]
        target_height = 48
        target_width = max(1, min(320, round(width * target_height / max(1, height))))
        resized = cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_CUBIC)
        canvas = np.full((target_height, 320), 255, dtype=np.uint8)
        canvas[:, :target_width] = resized
        normalized = (canvas.astype(np.float32) / 255.0 - 0.5) / 0.5
        return np.stack((normalized, normalized, normalized), axis=0)[None, ...]

    def _infer_text(self, image: np.ndarray) -> tuple[str, float]:
        if not self._ensure_loaded():
            return "", 0.0
        assert self._net is not None
        assert self._characters is not None
        self._net.setInput(self._preprocess(image))
        output = np.asarray(self._net.forward())[0]
        # OpenCV returns [time, classes] for this PP-OCRv5 model.
        if output.ndim != 2:
            return "", 0.0
        ids = output.argmax(axis=1)
        scores = output.max(axis=1)
        parts: list[str] = []
        selected_scores: list[float] = []
        previous = -1
        for index, score in zip(ids, scores):
            index = int(index)
            if index == 0 or index == previous:
                previous = index
                continue
            if index >= len(self._characters):
                previous = index
                continue
            parts.append(self._characters[index])
            selected_scores.append(float(score))
            previous = index
        confidence = float(np.mean(selected_scores)) if selected_scores else 0.0
        return "".join(parts), confidence

    def recognize(
        self,
        frame: Optional[np.ndarray],
        red: np.ndarray,
        band_start: int,
        band_end: int,
        detector_cfg: Mapping[str, Any],
    ) -> Optional[tuple[float, str, float]]:
        """Return ``(value, text, confidence)`` or ``None`` if inconclusive."""
        if not self._ensure_loaded():
            return None
        roi = tuple(int(value) for value in detector_cfg["red_attribute_roi"])
        roi_x1, roi_y1, _, _ = roi
        if red.shape[1] <= 0:
            return None

        padding = max(2, min(8, round((band_end - band_start) * 0.4)))
        start = max(0, int(band_start) - padding)
        end = min(red.shape[0], int(band_end) + padding)
        if end <= start:
            return None
        # Give OCR the complete attribute row. The template recognizer uses a
        # narrower value ROI, but cutting off the left edge of a digit can turn
        # a 9 into an 8 after window scaling. The decoder below keeps only the
        # final number immediately before "%".
        mask_crop = np.asarray(red[start:end, :], dtype=bool)
        if frame is not None:
            frame = np.asarray(frame)
            y1 = max(0, roi_y1 + int(band_start) - padding)
            y2 = min(frame.shape[0], roi_y1 + int(band_end) + padding)
            x1 = max(0, roi_x1)
            x2 = min(frame.shape[1], roi_x1 + red.shape[1])
            crop = frame[y1:y2, x1:x2]
        else:
            crop = mask_crop.astype(np.uint8) * 255
        if crop.size == 0:
            return None

        signature = (crop.shape, hash(crop.tobytes()), hash(mask_crop.tobytes()))
        now = time.monotonic()
        cached = self._cache.get(signature)
        if cached and now - cached[0] < 2.0:
            return cached[1]

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        text, confidence = self._infer_text(gray)
        matches = list(_PERCENT_RE.finditer(text))
        if not matches:
            isolated = np.where(mask_crop, 0, 255).astype(np.uint8)
            text, confidence = self._infer_text(isolated)
            matches = list(_PERCENT_RE.finditer(text))
        result: Optional[tuple[float, str, float]] = None
        if matches:
            token = matches[-1].group(1).replace(",", ".")
            try:
                value = float(token)
            except ValueError:
                value = -1.0
            if 0.0 <= value <= 100.0:
                result = (value, f"{token}%", confidence)
        self._cache[signature] = (now, result)
        if len(self._cache) > 64:
            oldest = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest, None)
        return result


def install_digit_ocr_fallback(detector: Any, feature_cfg: Mapping[str, Any]) -> bool:
    """Attach the OCR fallback to the existing compiled detector instance."""
    if getattr(detector, "_digit_ocr_fallback", None) is not None:
        return True
    engine = RedPercentageOCR(feature_cfg)
    if not engine.available:
        return False

    original_analyze = detector.analyze
    original_read = detector._read_red_percentage

    def analyze(self: Any, frame: np.ndarray):
        self._digit_ocr_frame = np.asarray(frame)
        return original_analyze(frame)

    def read(self: Any, red: np.ndarray, band_start: int, band_end: int):
        baseline = original_read(red, band_start, band_end)
        _, _, baseline_confidence = baseline
        minimum = float(self.cfg.get("red_attribute_min_digit_confidence", 0.44))
        # Keep the zero-cost template path for clear digits. OCR is reserved
        # for cases that previously led to a wrong percentage or no result.
        should_fallback = (
            baseline[0] is None
            or "?" in baseline[1]
            or baseline_confidence < max(0.66, minimum + 0.18)
        )
        if should_fallback:
            recognized = engine.recognize(
                getattr(self, "_digit_ocr_frame", None),
                red,
                band_start,
                band_end,
                self.cfg,
            )
            if recognized and recognized[2] >= 0.62:
                return recognized
        return baseline

    detector._digit_ocr_fallback = engine
    detector.analyze = types.MethodType(analyze, detector)
    detector._read_red_percentage = types.MethodType(read, detector)
    return True
