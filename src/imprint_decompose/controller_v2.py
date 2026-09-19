"""Wait for enhancement results before deciding whether to keep an imprint."""

import time

import win32gui

from .controller import (
    BotError,
    ImprintDecomposeController as BaseController,
    Rect,
    WindowInfo,
    WindowLocator,
)


class ImprintWindowLocator(WindowLocator):
    """Prefer the portrait game window; adapt a maximized wide host window."""

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
            viewport_width = max(min_w, round(source.rect.height * expected))
            viewport_width = min(viewport_width, source.rect.width)
            inset = (source.rect.width - viewport_width) // 2
            best = WindowInfo(
                hwnd=source.hwnd,
                title=source.title,
                rect=Rect(
                    source.rect.left + inset,
                    source.rect.top,
                    source.rect.left + inset + viewport_width,
                    source.rect.bottom,
                ),
            )
        else:
            self._last_hwnd = None
            return None
        self._last_hwnd = best.hwnd
        return best


def enhancement_keep_reason(values, unreadable, threshold, originals, current):
    """Return a keep reason only when the configured rule is satisfied."""
    qualifying = tuple(value for value in values if value >= threshold)
    if qualifying:
        operator = '=' if threshold == 27 else '≥'
        return f'红色词条 {max(qualifying):g}% {operator} {threshold:g}%'
    if unreadable:
        return '红色词条数值无法确认'
    if originals and current.issubset(originals):
        return '强化后没有第三种元素颜色'
    return None


class ImprintDecomposeController(BaseController):
    def __init__(self, cfg, feature_cfg, *, dry_run=False, on_stats=None):
        super().__init__(cfg, feature_cfg, dry_run=dry_run, on_stats=on_stats)
        self.locator = ImprintWindowLocator(cfg)
        self.input.locator = self.locator
        self._enhancement_red_threshold = 20.0
        self._update_enhancement_status()

    def _reset_enhancement_session(self):
        self._result_signature = None
        self._result_seen = 0
        return super()._reset_enhancement_session()

    def _handle_detail(self, window, frame, analysis):
        detail = analysis.detail
        if not (self._enhancement_is_enabled() and detail and detail.right_ready
                and analysis.dismantle_ready):
            return super()._handle_detail(window, frame, analysis)

        target, completed = self._enhancement_progress()
        if completed:
            expected = 2 + completed
            if (detail.right_filled_count < expected
                    or len(detail.right_combination) < expected):
                self._result_signature = None
                self._result_seen = 0
                elapsed = time.monotonic() - (self._phase_since or time.monotonic())
                if elapsed > float(self.feature_cfg['actions']['enhancement_timeout']):
                    self._save_debug(frame, analysis, reason='enhancement_slots_not_updated')
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
                originals = set(self._enhancement_original_elements or ())
                current = set(detail.right_combination)
                threshold = float(self.stats.red_attribute_threshold)
                unreadable = analysis.red_attribute_unreadable_count
                reason = enhancement_keep_reason(
                    analysis.red_attribute_values, unreadable, threshold,
                    originals, current,
                )
                if reason:
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
