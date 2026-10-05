"""Bounded, replayable evidence: raw pixels are separate from overlays."""
from dataclasses import asdict
import json

import cv2

from .auto_vision import list_image


def save_scan_failure(frame, scan, run, timings):
    from tower_bot.config import debug_dir
    folder = debug_dir()
    folder.mkdir(parents=True, exist_ok=True)
    prefix = 'imprint_auto_failure_'
    images = {'raw': frame}
    if run.identity_reference is not None:
        images['identity_before'] = run.identity_reference
        images['identity_after'] = run._identity(frame)
    if run.scroll_before is not None:
        images['before'] = run.scroll_before
    if scan is not None:
        images['after'] = list_image(frame, scan.roi)
    written = {}
    for name, pixels in images.items():
        ok, data = cv2.imencode('.png', pixels)
        if not ok:
            raise OSError(f'无法编码原始诊断图 {name}')
        filename = prefix + name + '_latest.png'
        (folder / filename).write_bytes(data.tobytes())
        written[name] = filename
    # Metadata identifies the files belonging to THIS failure. Older before
    # images may remain, but are never named here if no scroll was attempted.
    report = {
        'phase': getattr(run, 'failed_phase', run.phase).name,
        'message': run.message, 'scrolls': run.scrolls, 'pass': run.pass_number,
        'seeking_top': run.seeking_top, 'scroll_kind': run.scroll_kind,
        'motion': run.last_motion, 'stable_frames': run.stable_frames,
        'readable_frames': run.readable_frames, 'timings_ms': timings,
        'identity_roi': run.identity_roi, 'identity_difference': run.identity_difference,
        'scan': asdict(scan) if scan is not None else None, 'images': written,
    }
    (folder / (prefix + 'latest.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
