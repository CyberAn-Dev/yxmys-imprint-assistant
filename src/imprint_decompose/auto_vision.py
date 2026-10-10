"""Read five-column inventory slot strips, without fixed row coordinates.

Only complete, positively identified strips may authorize selection. Grey is
evidence, not the absence of a detected colour. Coordinates remain in the
capture's reference system; cropped test pictures use an explicit ROI.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from .models import ELEMENT_ORDER


@dataclass(frozen=True)
class ScanCard:
    x: int
    y: int
    slot_y: int
    pitch: float
    elements: tuple[str, ...]
    complete: bool
    column: int = -1

    @property
    def filled(self):
        return len(self.elements)

    @property
    def eligible(self):
        return self.complete and self.filled == 2


@dataclass(frozen=True)
class ListScan:
    cards: tuple[ScanCard, ...]
    roi: tuple[int, int, int, int]
    uncertain: bool = False
    short_page: bool = False
    empty: bool = False
    clipped_bottom: bool = False
    issues: tuple[str, ...] = ()

    @property
    def candidates(self):
        return tuple(card for card in self.cards if card.eligible)

    @property
    def signature(self):
        return tuple((c.x, c.slot_y, c.elements, c.complete) for c in self.cards)

    @property
    def stability_key(self):
        # Subpixel resampling may change rounded centres or reverse two
        # columns' y order. Image stability still guards real movement.
        rows = []
        for c in sorted(self.cards, key=lambda c: (c.slot_y, c.x)):
            if not rows or abs(c.slot_y - rows[-1][0].slot_y) > 3:
                rows.append([])
            rows[-1].append(c)
        return tuple(tuple((c.column, c.elements, c.complete)
                           for c in sorted(row, key=lambda c: c.x)) for row in rows)


def _element(hues):
    # Same hue bands as the established detail recognizer. Use the dominant
    # hue, not a mean across the red wraparound or white highlight pixels.
    if not len(hues):
        return None
    hue = int(np.argmax(np.bincount(hues.astype(int), minlength=180)))
    ranges = ((78, 105), (0, 12), (105, 127), (128, 151), (13, 30))
    if hue >= 170:
        return ELEMENT_ORDER[1]
    for name, (lo, hi) in zip(ELEMENT_ORDER, ranges):
        if lo <= hue <= hi:
            return name
    return None


def _slot_outer_radius(pitch):
    """Integer footprint used to verify a slot, including its dark outline."""
    return max(max(3, round(pitch * .25)) + 2, round(pitch * .5))


def _bottom_slots_visible(card, bottom):
    return card.slot_y + _slot_outer_radius(card.pitch) < bottom - 1


def inventory_viewport(frame, roi):
    """Exclude an intruding fixed bottom navigation bar, not a partial row.

    The configured ROI can extend into the nav when the host's title bar
    changes the normalized height. Require both a broad dark separator and
    a dark band BELOW parchment; isolated badges/card shadows cannot trim it.
    The controller freezes this viewport for the run, before any scrolling.
    """
    x0, y0, x1, y1 = map(int, roi)
    if not (0 <= x0 < x1 <= frame.shape[1] and 0 <= y0 < y1 <= frame.shape[0]):
        raise ValueError('刻印列表识别区域超出截图边界')
    hsv = cv2.cvtColor(frame[:, x0:x1], cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    dark = (s < 100) & (v < 85)
    parchment = (s >= 20) & (s < 90) & (v >= 90) & (v <= 220)
    for y in range(max(y0+180, y1-65), min(y1, len(hsv)-15)):
        # A full bottom row can cover most of the parchment ABOVE the nav.
        # Its fixed side gutters are not covered by card art/slots. Requiring
        # >55% parchment across the whole width made the viewport depend on
        # the inventory contents, and exposed hidden slots behind the nav.
        above = parchment[y-15:y-3]
        gutters = np.concatenate((above[:, :12], above[:, -12:]), axis=1)
        parchment_above = above.mean() >= .55 or gutters.mean() >= .75
        if (dark[y].mean() >= .85 and dark[y+3:y+15].mean() >= .75
                and parchment_above):
            return (x0, y0, x1, y)
    return (x0, y0, x1, y1)


def read_inventory(frame, roi):
    """Find aligned 2..5 active slots followed by explicit grey hexagons.

    Connected colour components supply actual slot positions and spacing.
    The strip's centre gives the card's column; its y supplies the scrolling
    row. Imprint artwork (star/square/triangle) is deliberately not a template.
    """
    x0, y0, x1, y1 = map(int, roi)
    height, width = frame.shape[:2]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError('刻印列表识别区域超出截图边界')
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    area = hsv[y0:y1, x0:x1]
    mask = ((area[:, :, 1] >= 90) & (area[:, :, 2] >= 65)).astype(np.uint8)
    _, labels, boxes, centers = cv2.connectedComponentsWithStats(mask)
    components = []
    widths = []
    for (x, y, w, h, size), (cx, cy) in zip(boxes[1:], centers[1:]):
        if 6 <= w <= 22 and 7 <= h <= 23 and 35 <= size <= 340 and .55 <= w / h <= 1.5:
            components.append((float(cx + x0), float(cy + y0)))
            widths.append(w)
    # Bilinear capture scaling can join adjacent yellow/purple hexagons by
    # one saturated edge pixel. Split wide, slot-height components into cells.
    nominal_pitch = float(np.median(widths)) + 2 if widths else 15
    for label, (x, y, w, h, size) in enumerate(boxes[1:], 1):
        if not (23 <= w <= 100 and 7 <= h <= 23 and size >= 70):
            continue
        count = round(w / nominal_pitch)
        if not 2 <= count <= 5 or not 11 <= w / count <= 19:
            continue
        for part in range(count):
            left, right = round(x + part*w/count), round(x + (part+1)*w/count)
            py, px = np.where(labels[y:y+h, left:right] == label)
            if len(px) >= 35:
                components.append((float(px.mean()+left+x0), float(py.mean()+y+y0)))
    rows = []
    for cx, cy in sorted(components, key=lambda p: p[1]):
        if not rows or abs(cy - np.median([p[1] for p in rows[-1]])) > 3:
            rows.append([])
        rows[-1].append((cx, cy))
    cards = []
    uncertain = False
    issues = []
    invalid_strips = []
    clipped_bottom = False
    for row in rows:
        groups = []
        for point in sorted(row):
            if not groups or point[0] - groups[-1][-1][0] > 23:
                groups.append([])
            groups[-1].append(point)
        row_cards = []
        for group in groups:
            if not 2 <= len(group) <= 5:
                continue
            gaps = np.diff([p[0] for p in group])
            pitch = float(np.median(gaps))
            if not 11 <= pitch <= 19 or np.max(np.abs(gaps - pitch)) > 2.2:
                continue
            start = group[0][0]
            sy = int(round(np.median([p[1] for p in group])))
            cx = int(round(start + 2 * pitch))
            if start - pitch / 2 < x0 or start + 4.5 * pitch >= x1:
                continue
            if sy - pitch / 2 < y0 or sy + pitch / 2 >= y1:
                continue
            elements = []
            valid = True
            for index in range(5):
                sx = int(round(start + index * pitch))
                radius = max(3, round(pitch * .25))
                patch = hsv[sy-radius:sy+radius+1, sx-radius:sx+radius+1]
                active = (patch[:, :, 1] >= 90) & (patch[:, :, 2] >= 65)
                if index < len(group):
                    element = _element(patch[:, :, 0][active])
                    if active.mean() < .38 or element is None:
                        valid = False
                        break
                    elements.append(element)
                else:
                    # Neutral grey centre AND its dark outline are required.
                    grey = ((patch[:, :, 1] < 42) & (patch[:, :, 2] >= 55)
                            & (patch[:, :, 2] <= 155))
                    outer = _slot_outer_radius(pitch)
                    border = hsv[sy-outer:sy+outer+1, sx-outer:sx+outer+1, 2]
                    if grey.mean() < .70 or active.mean() > .08 or (border < 85).mean() < .12:
                        valid = False
                        break
            # Judge the pixels actually sampled, not a fractional estimate
            # plus a second margin. A fully visible final strip can sit just
            # above the nav; the old test rejected it by as little as .34 px.
            complete = (sy - pitch * 5 >= y0 + 2
                        and sy + _slot_outer_radius(pitch) < y1 - 1)
            if valid:
                row_cards.append(ScanCard(cx, round(sy - pitch * 2.7), sy,
                                          pitch, tuple(elements), complete))
            elif complete:
                invalid_strips.append((cx, sy, pitch))
        cards.extend(row_cards)
    cards.sort(key=lambda c: (c.slot_y, c.x))
    for cx, sy, pitch in invalid_strips:
        # A fragment within one slot pitch of the viewport bottom cannot
        # establish a complete card. Keep it as a clipped-boundary signal,
        # not a failure of the fully visible rows above. It never becomes
        # a ScanCard or authorizes selection/completion.
        if sy + pitch >= y1 - 1:
            clipped_bottom = True
            continue
        # Tiny red fragments in a triangle/star or its level badge can mimic
        # the start of a strip. Only discard them when a FULLY validated five
        # slot strip places the fragment inside that same card's artwork.
        # Missing grey slots at the real strip's height remain an error.
        if any(abs(c.x-cx) <= c.pitch*1.1
               and c.slot_y-c.pitch*3.5 < sy < c.slot_y-c.pitch for c in cards):
            continue
        uncertain = True
        issues.append(f'槽位不完整@{cx},{sy}')
    # Validate the five-column lattice from observed positions, not from the
    # supplied cropped screenshots. A trailing partial inventory row is OK.
    if cards:
        pitch = float(np.median([c.pitch for c in cards]))
        observed = []
        for a in cards:
            neighbors = [b.x - a.x for b in cards
                         if abs(b.slot_y-a.slot_y) <= 3
                         and pitch * 5.5 <= b.x-a.x <= pitch * 7.3]
            if neighbors:
                observed.append(min(neighbors))
        column_pitch = float(np.median(observed)) if observed else pitch * 6.4
        if not pitch * 5.5 <= column_pitch <= pitch * 7.3:
            uncertain = True
            issues.append('列间距异常')
        first = min(c.x for c in cards)
        # Expected first column is near 1/8th of the visible list width.
        if first > x0 + (x1 - x0) * .23:
            uncertain = True
            issues.append('未识别到第一列')
        numbered = []
        for c in cards:
            column = round((c.x - first) / column_pitch)
            if not 0 <= column <= 4 or abs(c.x - (first + column * column_pitch)) > pitch * .55:
                uncertain = True
                issues.append(f'列位置异常@{c.x},{c.slot_y}')
            numbered.append(ScanCard(c.x, c.y, c.slot_y, c.pitch, c.elements, c.complete, column))
        cards = numbered
        clipped_bottom = clipped_bottom or any(not _bottom_slots_visible(c, y1) for c in cards)
        # Do not mistake a missed middle/right card for an incomplete final
        # inventory row. Artwork only flags missing recognition; it never
        # authorizes selecting a card or infers its attribute count.
        red = (((area[:, :, 0] <= 12) | (area[:, :, 0] >= 170))
               & (area[:, :, 1] >= 80) & (area[:, :, 2] >= 70)).astype(np.uint8)
        _, _, art_boxes, art_centers = cv2.connectedComponentsWithStats(red)
        # Artwork centroids vary by imprint shape. Reserve enough space for
        # the entire slot strip, not just its centre (live bottom rows can
        # lose the last few pixels behind navigation). Such rows are never
        # proof of completion or permission to select unrecognized cards.
        for (_, _, w, h, size), (cx, cy) in zip(art_boxes[1:], art_centers[1:]):
            cx, cy = cx+x0, cy+y0
            if (size >= 250 and 25 <= w <= 100 and 15 <= h <= 100
                    and cy > y1-pitch*5 and cy+pitch*3.3 >= y1-2):
                # Artwork shape/centroid is only a missing-strip hint. A
                # positively recognized five-slot strip on THIS card takes
                # precedence over the rough artwork-to-slot height estimate.
                matched = any(abs(c.x-cx) < c.pitch*1.5
                              and abs(c.y-cy) < c.pitch*1.7
                              and c.slot_y > cy
                              and _bottom_slots_visible(c, y1) for c in cards)
                if not matched:
                    clipped_bottom = True
            if (size >= 250 and 25 <= w <= 100 and 25 <= h <= 100
                    and cy - pitch*2.3 > y0+2 and cy + pitch*3.3 < y1-2):
                if not any(abs(c.x-cx) < pitch*1.5 and abs(c.y-cy) < pitch*1.7 for c in cards):
                    # Other positively identified cards in the same row
                    # establish its real slot height more accurately than
                    # the shape-dependent artwork centroid estimate.
                    row = [c for c in cards if abs(c.y-cy) < pitch*1.7]
                    if row and all(not _bottom_slots_visible(c, y1) for c in row):
                        clipped_bottom = True
                        continue
                    uncertain = True
                    issues.append(f'卡片槽位漏识别@{round(cx)},{round(cy)}')
    short_page = False
    if cards and not uncertain and all(c.complete for c in cards):
        pitch = float(np.median([c.pitch for c in cards]))
        first_top = min(c.slot_y for c in cards) - pitch*5
        blank_from = round(max(c.slot_y for c in cards) + pitch)
        # Normalized live windows include up to 24 px of fixed toolbar at
        # the ROI top. Two complete rows may leave only ~56 px of parchment,
        # which still covers the start/artwork of any following row. This
        # is only a visual hint: AutoRun also verifies both wheel directions.
        header_guard = 24 if y1-y0 >= 180 else 0
        if (not clipped_bottom and 0 <= first_top-y0 <= pitch*2.5+header_guard
                and y1-blank_from >= pitch*3.5
                and _parchment_empty(hsv[blank_from:y1, x0:x1])):
            short_page = True
    return ListScan(tuple(cards), (x0, y0, x1, y1), uncertain, short_page,
                    clipped_bottom=clipped_bottom, issues=tuple(issues))


def _parchment_empty(hsv):
    if not hsv.size:
        return False
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    parchment = (s >= 20) & (s < 90) & (v >= 90) & (v <= 220)
    return bool(parchment.mean() >= .90 and ((s >= 90) & (v >= 65)).mean() < .01)


def blank_inventory(frame, roi):
    x0, y0, x1, y1 = roi
    return _parchment_empty(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV))


def list_chrome(frame, roi):
    """Stable toolbar/nav edges, excluding the changing capacity counter."""
    x0, y0, x1, y1 = roi
    top, bottom = max(0, y0-40), min(frame.shape[0], y1+30)
    parts = [frame[top:y0, x0:x0+110], frame[top:y0, x1-110:x1],
             frame[y1:bottom, x0:x0+110], frame[y1:bottom, x1-110:x1]]
    return np.concatenate([p.reshape(-1, 3) for p in parts]).reshape(-1, 1, 3)


def list_image(frame, roi):
    x0, y0, x1, y1 = roi
    # Full colour distinguishes many otherwise identical-looking rows. No
    # artwork/color-combination digest is used as a unique item identifier.
    return cv2.GaussianBlur(frame[y0:y1, x0:x1], (3, 3), 0)


def image_distance(a, b):
    if a is None or b is None or a.shape != b.shape:
        return float('inf')
    return float(np.mean(cv2.absdiff(a, b)))


def scroll_motion_limit(height):
    """Maximum verifiable shift after excluding fixed chrome and one row."""
    guard = 24 if height >= 180 else 0
    return max(0, height-guard-90)


def scroll_displacement(before, after, max_shift):
    """Return an unambiguous vertical displacement, or None.

    Must retain at least one complete row of overlap. Periodic/identical rows
    are intentionally not taken as proof of movement or of reaching an end.
    """
    if before.shape != after.shape:
        return None
    # The live normalized ROI may include the bottom edge of the fixed
    # filter toolbar. Comparing it against scrolling artwork creates a false
    # competing displacement exactly one repeated card-row away. Exclude a
    # narrow top guard band from BOTH images, and still require a full row
    # of overlap in the remaining moving area. Slot eligibility is unchanged.
    if before.shape[0] >= 180:
        before, after = before[24:], after[24:]
    height = before.shape[0]
    scores = []
    for dy in range(-min(max_shift, height - 90), min(max_shift, height - 90) + 1):
        # Live single-notch boundary probes can move only 3-4 normalized
        # pixels. Excluding all shifts below five hid their best match and
        # let a repeated row compete with the wrong displacement instead.
        if abs(dy) < 2:
            continue
        left = before[max(0, -dy):min(height, height-dy)]
        right = after[max(0, dy):min(height, height+dy)]
        scores.append((image_distance(left, right), dy))
    if not scores:
        return None
    scores.sort()
    best, dy = scores[0]
    competitor = min((score for score, other in scores if abs(other-dy) > 7), default=100)
    if best > 9 or best + 1.5 >= competitor or best >= image_distance(before, after) * .75:
        return None
    return dy
