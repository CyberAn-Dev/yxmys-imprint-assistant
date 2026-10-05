"""Wait for enhancement results before deciding whether to keep an imprint."""

import time

import win32gui

from .game_digit_templates import install_game_digit_templates
from .digit_ocr_fallback import install_digit_ocr_fallback
from .detector_runtime import install_state_first_analysis
from .auto_controller import AutoControllerMixin
from .controller import (
    BotError,
    ImprintDecomposeController as BaseController,
    Rect,
    WindowInfo,
    WindowLocator,
)


class ImprintWindowLocator(WindowLocator):
    """Prefer the portrait game window; adapt a maximized wide host window."""

    def _viewport_rect(self, rect):
        window_cfg = self.cfg['window']
        expected = float(window_cfg['reference_width']) / float(window_cfg['reference_height'])
        tolerance = float(window_cfg.get('aspect_ratio_tolerance', 0.05))
        if rect.width > rect.height * expected * (1 + tolerance):
            width = min(rect.width, max(int(window_cfg['minimum_width']), round(rect.height * expected)))
            inset = (rect.width - width) // 2
            return Rect(rect.left + inset, rect.top, rect.left + inset + width, rect.bottom)
        return rect

    def current_rect(self, hwnd):
        # Capture and input must use the same viewport, including after a move.
        return self._viewport_rect(super().current_rect(hwnd))

    def find(self):
        window_cfg = self.cfg['window']
        title_key = str(window_cfg['title_contains'])
        min_w = int(window_cfg['minimum_width'])
        min_h = int(window_cfg['minimum_height'])
        expected = float(window_cfg['reference_width']) / float(window_cfg['reference_height'])
        tolerance = float(window_cfg.get('aspect_ratio_tolerance', 0.05))
        natural = []
        wide = []

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ''
            if title_key not in title or '刻印快速筛选分解小助手' in title:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            rect = Rect(left, top, right, bottom)
            if rect.width < min_w or rect.height < min_h:
                return
            info = WindowInfo(hwnd=hwnd, title=title, rect=rect)
            ratio_error = abs(rect.width / rect.height - expected) / expected
            if ratio_error <= tolerance:
                natural.append(info)
            elif rect.width / rect.height > expected:
                wide.append(info)

        win32gui.EnumWindows(callback, None)
        if natural:
            best = max(natural, key=lambda item: item.rect.area)
        elif wide:
            source = max(wide, key=lambda item: item.rect.area)
            best = WindowInfo(
                hwnd=source.hwnd,
                title=source.title,
                rect=self._viewport_rect(source.rect),
            )
        else:
            self._last_hwnd = None
            return None
        best = WindowInfo(hwnd=best.hwnd, title=best.title, rect=self._viewport_rect(best.rect))
        self._last_hwnd = best.hwnd
        return best


def _slot_alignment_quality(slots, slot_y, nominal_y):
    """Score a candidate slot row without rewarding weak false positives."""
    known = sum(1 for slot in slots if slot.active and slot.element)
    unknown = sum(1 for slot in slots if slot.active and not slot.element)
    strength = sum(
        float(slot.active_ratio)
        for slot in slots
        if slot.active and slot.element
    )
    # A row far from the configured element row is usually a decorative icon or
    # a text row.  Keep a small tolerance for window scaling, then penalize a
    # distant row sharply so a red title cannot beat two real element slots.
    distance = abs(int(slot_y) - int(nominal_y))
    distance_penalty = (
        0.02 * min(distance, 25)
        + 0.12 * max(0, distance - 25)
    )
    return known * 0.75 + strength - unknown * 0.25 - distance_penalty


def install_detail_slot_alignment(detector, feature_cfg):
    """Compensate for a small horizontal viewport margin in the game window.

    Some window hosts capture a black/transparent strip on the left of the
    actual game canvas.  The original fixed coordinates then land on the next
    row of the card and can report false red elements.  We calibrate the slot
    coordinates against both detail cards and reuse the offset until the view
    changes.  The detector still returns slots in their original x order.
    """
    vision = feature_cfg['vision']
    left_x = tuple(int(value) for value in vision['detail_slot_x_left'])
    right_x = tuple(int(value) for value in vision['detail_slot_x_right'])
    nominal_y = int(vision['detail_slot_y'])
    original_read_slots = detector._read_slots
    state = {'offset': None, 'calibrated_at': 0.0, 'pending': None}
    min_filled = int(vision.get('min_filled_slots', 2))

    def likely_detail(hsv):
        """Avoid an expensive calibration scan while the list is visible."""
        panel_threshold = float(vision.get('panel_parchment_ratio_threshold', 0.55))
        star_threshold = float(vision.get('detail_star_ratio_threshold', 0.1))
        return (
            detector._parchment_ratio(hsv, vision['left_panel_roi']) >= panel_threshold
            and detector._parchment_ratio(hsv, vision['right_panel_roi']) >= panel_threshold
            and detector._red_ratio(hsv, vision['left_star_roi']) >= star_threshold
            and detector._red_ratio(hsv, vision['right_star_roi']) >= star_threshold
        )

    def read_candidate(hsv, offset):
        left = original_read_slots(hsv, [value + offset for value in left_x])
        right = original_read_slots(hsv, [value + offset for value in right_x])
        left_slots, left_y = left
        right_slots, right_y = right
        score = (
            _slot_alignment_quality(left_slots, left_y, nominal_y)
            + _slot_alignment_quality(right_slots, right_y, nominal_y)
        )
        return score, left, right

    def candidate_is_detail(candidate):
        _, left, right = candidate
        left_slots, left_y = left
        right_slots, right_y = right
        right_known = sum(1 for slot in right_slots if slot.active and slot.element)
        return (
            right_known >= min_filled
            and abs(int(left_y) - nominal_y) <= 25
            and abs(int(right_y) - nominal_y) <= 25
        )

    def search_alignment(hsv):
        selected = None
        selected_offset = 0
        for offset in range(-48, 49, 4):
            candidate = read_candidate(hsv, offset)
            if selected is None or candidate[0] > selected[0]:
                selected = candidate
                selected_offset = offset
        state['offset'] = selected_offset
        state['calibrated_at'] = time.monotonic()
        return selected

    def aligned_read_slots(hsv, x_values):
        values = tuple(int(value) for value in x_values)
        if values == left_x:
            side = 'left'
        elif values == right_x:
            side = 'right'
        else:
            return original_read_slots(hsv, x_values)

        token = id(hsv)
        pending = state['pending']
        if pending is None or pending['token'] != token:
            now = time.monotonic()
            detail_hint = likely_detail(hsv)
            if not detail_hint:
                # The current screen is normally the list.  Keep the last
                # offset without rescanning dozens of rows on every frame.
                selected = read_candidate(hsv, state['offset'] or 0)
            elif (
                state['offset'] is not None
                and now - state['calibrated_at'] < 4.0
            ):
                selected = read_candidate(hsv, state['offset'])
                if not candidate_is_detail(selected):
                    selected = search_alignment(hsv)
            else:
                selected = search_alignment(hsv)
            pending = {
                'token': token,
                'left': selected[1],
                'right': selected[2],
            }
            state['pending'] = pending

        result = pending[side]
        if side == 'right':
            state['pending'] = None
        return result

    detector._read_slots = aligned_read_slots
    detector._detail_slot_alignment_state = state


def red_attribute_meets_threshold(values, threshold):
    """All options mean at least the selected percentage, including 27%."""
    return any(value >= threshold for value in values)


def enhancement_keep_reason(values, unreadable, threshold, originals, current):
    """Return a keep reason only when the configured rule is satisfied."""
    qualifying = tuple(value for value in values if value >= threshold)
    if qualifying:
        return f'红色词条 {max(qualifying):g}% ≥ {threshold:g}%'
    if unreadable:
        return '红色词条数值无法确认'

    # A detected red value that is below the threshold must not be rescued by
    # the color-combination rule.
    if values:
        return None

    original_elements = tuple(originals or ())
    original_set = set(original_elements)
    current_set = set(current or ())
    if (original_set and original_set.issubset(current_set)
            and len(current_set) <= 2):
        if len(original_set) == 1 and len(current_set) == 2:
            return '原始组合为重复颜色，强化后仅新增一种颜色'
        return '强化后没有第三种元素颜色'
    return None


def enhancement_slot_plan(filled_count, target):
    """Return (existing rounds, remaining clicks, protect as over-target)."""
    target = max(1, min(3, int(target)))
    filled_count = max(2, min(5, int(filled_count)))
    existing = filled_count - 2
    return existing, max(0, target - existing), existing > target


class ImprintDecomposeController(AutoControllerMixin, BaseController):
    _auto_keep_policy = staticmethod(enhancement_keep_reason)

    def __init__(self, cfg, feature_cfg, *, dry_run=False, on_stats=None):
        self._red_threshold_counted = False
        super().__init__(cfg, feature_cfg, dry_run=dry_run, on_stats=on_stats)
        self.locator = ImprintWindowLocator(cfg)
        self.input.locator = self.locator
        install_detail_slot_alignment(self.detector, feature_cfg)
        install_game_digit_templates(self.detector)
        install_state_first_analysis(self.detector)
        install_digit_ocr_fallback(self.detector, feature_cfg)
        self._enhancement_red_threshold = 20.0
        self.stats.red_threshold_matches = 0
        self._update_enhancement_status()

    def _red_attribute_meets_threshold(self, analysis):
        with self._auto_settings_lock:
            threshold = self._enhancement_red_threshold
        return red_attribute_meets_threshold(analysis.red_attribute_values, threshold)

    def _reset_enhancement_session(self):
        self._manual_origin_filled = None
        self._manual_enhancement_sent = False
        self._red_threshold_counted = False
        self._result_signature = None
        self._result_seen = 0
        self._initial_existing_enhancements = None
        self._initial_detail_signature = None
        self._initial_detail_seen = 0
        self._plain_detail_signature = None
        self._plain_detail_seen = 0
        return super()._reset_enhancement_session()

    def _count_red_threshold_match(self, analysis):
        """Count one qualifying imprint once during its detail session."""
        if self._red_threshold_counted:
            return
        if not self._red_attribute_meets_threshold(analysis):
            return
        self._red_threshold_counted = True
        self.stats.red_threshold_matches = (
            getattr(self.stats, 'red_threshold_matches', 0) + 1
        )
        self._emit_stats()

    def _handle_detail(self, window, frame, analysis):
        detail = analysis.detail
        if detail and detail.right_ready and analysis.dismantle_ready:
            if detail.right_filled_count == 5 and not getattr(self, '_manual_enhancement_sent', False):
                raise BotError('原本已有 5 个属性的刻印禁止分解；请返回列表选择未强化刻印')
            if getattr(self, '_manual_origin_filled', None) is None:
                self._manual_origin_filled = detail.right_filled_count
        if (not self._enhancement_is_enabled() and detail and detail.right_ready
                and analysis.dismantle_ready):
            signature = (tuple(detail.right_combination), detail.right_filled_count,
                         tuple(analysis.red_attribute_values), analysis.red_attribute_unreadable_count)
            if signature != getattr(self, '_plain_detail_signature', None):
                self._plain_detail_signature = signature
                self._plain_detail_seen = 1
                self.stats.last_action = '等待刻印与词条识别稳定'
                self._emit_stats()
                return
            self._plain_detail_seen += 1
            if self._red_attribute_meets_threshold(analysis) or analysis.red_attribute_unreadable_count:
                if self._red_attribute_meets_threshold(analysis):
                    self._count_red_threshold_match(analysis)
                    reason = f'红色词条达到 ≥{self._enhancement_red_threshold:g}%'
                else:
                    reason = '红色词条数值无法确认，保护保留'
                self._record_operation(f'KEEP_DECISION red_values={list(analysis.red_attribute_values)} reason={reason}')
                self._keep_detail(window, frame, analysis, reason=reason, decision=reason)
                return
        if not (self._enhancement_is_enabled() and detail and detail.right_ready
                and analysis.dismantle_ready):
            if detail and self._red_attribute_meets_threshold(analysis):
                self._count_red_threshold_match(analysis)
            return super()._handle_detail(window, frame, analysis)

        if self._enhancement_current_combination is None:
            original_elements = tuple(
                slot.element for slot in detail.right_slots[:2]
                if slot.active and slot.element
            )
            initial_signature = (
                detail.right_filled_count,
                tuple(detail.right_combination),
                original_elements,
            )
            if initial_signature != self._initial_detail_signature:
                self._initial_detail_signature = initial_signature
                self._initial_detail_seen = 1
                self.stats.last_action = '正在确认已有强化次数与元素颜色'
                self._emit_stats()
                return
            self._initial_detail_seen += 1
            if self._initial_detail_seen < 2:
                return
            if detail.right_filled_count < 2 or len(original_elements) < 2:
                self.stats.last_action = '等待初始两个元素识别稳定'
                self._emit_stats()
                return

            self._ensure_enhancement_session(
                detail.right_combination,
                original_elements=original_elements,
            )
            target, _ = self._enhancement_progress()
            existing, remaining, over_target = enhancement_slot_plan(
                detail.right_filled_count, target,
            )
            self._initial_existing_enhancements = existing
            self._enhancement_completed = existing
            self.stats.enhancement_completed = existing
            if existing:
                expected_slots = 2 + target
                if over_target:
                    self.stats.last_action = (
                        f'检测到 {detail.right_filled_count}/5 个槽位，'
                        f'超过目标 {expected_slots}/5，进入保护判断'
                    )
                elif remaining:
                    self.stats.last_action = (
                        f'检测到已强化 {existing} 次，还需强化 {remaining} 次'
                    )
                else:
                    self.stats.last_action = (
                        f'检测到已强化 {existing} 次，已达到当前目标'
                    )
                self._update_enhancement_status(analysis)
                self._emit_stats()
                self._phase_since = time.monotonic() - 0.5

        target, completed = self._enhancement_progress()
        if completed:
            expected = 2 + completed
            if (detail.right_filled_count < expected
                    or len(detail.right_combination) < expected):
                self._result_signature = None
                self._result_seen = 0
                elapsed = time.monotonic() - (self._phase_since or time.monotonic())
                if elapsed > float(self.feature_cfg['actions']['enhancement_timeout']):
                    self._save_debug(frame, analysis, tag='enhancement_slots_not_updated')
                    raise BotError(f'强化后只识别到 {detail.right_filled_count}/{expected} 个槽位；已停止，未分解')
                self.stats.last_action = f'等待强化结果：{len(detail.right_combination)}/{expected} 个元素已识别'
                self._emit_stats()
                return

            if time.monotonic() - (self._phase_since or time.monotonic()) < 0.35:
                return

            signature = (completed, tuple(detail.right_combination),
                         tuple(analysis.red_attribute_values),
                         analysis.red_attribute_unreadable_count)
            if signature != getattr(self, '_result_signature', None):
                self._result_signature = signature
                self._result_seen = 1
                return
            self._result_seen += 1
            if self._result_seen < 2:
                return

            if completed >= target:
                originals = tuple(self._enhancement_original_elements or ())
                current = tuple(detail.right_combination)
                threshold = float(self.stats.red_attribute_threshold)
                unreadable = analysis.red_attribute_unreadable_count
                initial_existing = self._initial_existing_enhancements or 0
                if initial_existing > target:
                    reason = (
                        f'已强化 {initial_existing} 次，超过当前设置的 {target} 次'
                    )
                else:
                    reason = enhancement_keep_reason(
                        analysis.red_attribute_values, unreadable, threshold,
                        originals, current,
                    )
                if reason:
                    if reason.startswith('红色词条 '):
                        self._count_red_threshold_match(analysis)
                    self._record_operation(
                        f'KEEP_DECISION round={completed}/{target} slots={detail.right_filled_count} '
                        f'original={sorted(originals)} current={sorted(current)} '
                        f'red_values={list(analysis.red_attribute_values)} '
                        f'threshold={threshold:g} unreadable={unreadable} reason={reason}'
                    )
                    self._keep_detail(
                        window, frame, analysis, reason=reason,
                        decision=f'{reason}，已保留并退出详情',
                    )
                    return

        return super()._handle_detail(window, frame, analysis)

    def _handle_wait_confirm(self, window, analysis):
        if getattr(self, '_manual_origin_filled', None) not in (2, 3, 4):
            raise BotError('缺少本轮初始属性记录，禁止自动确认分解')
        return super()._handle_wait_confirm(window, analysis)

    def _click_enhancement(self, window, analysis):
        before = self._enhancement_completed
        result = super()._click_enhancement(window, analysis)
        if self._enhancement_completed > before:
            self._manual_enhancement_sent = True
        return result
