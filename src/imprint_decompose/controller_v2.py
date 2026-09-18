"""Wait for enhancement results before deciding whether to keep an imprint."""

import time

from .controller import BotError, ImprintDecomposeController as BaseController


class ImprintDecomposeController(BaseController):
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
                         analysis.red_attribute_count,
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
                red_count = (analysis.red_attribute_count
                             + analysis.red_attribute_unreadable_count)
                if red_count or (originals and current.issubset(originals)):
                    reason = ('出现红色词条' if red_count else '强化后没有第三种元素颜色')
                    self._record_operation(
                        f'KEEP_DECISION round={completed}/{target} slots={detail.right_filled_count} '
                        f'original={sorted(originals)} current={sorted(current)} '
                        f'red={red_count} reason={reason}'
                    )
                    self._keep_detail(
                        window, frame, analysis, reason=reason,
                        decision=f'{reason}，已保留并退出详情',
                    )
                    return

        return super()._handle_detail(window, frame, analysis)
