"""Fail-closed inventory workflow. No screen capture, input, threads or UI.

The adapter executes an Intent only after checking its generation, focus,
freshness and window geometry. State advances only on dispatch acknowledgement
and subsequent *observed* game transitions, never on a guessed click count.
"""
from dataclasses import dataclass
from enum import Enum, auto
import math

from .auto_vision import image_distance, list_image, scroll_displacement, scroll_motion_limit
from .models import ImprintState


class AutoSafetyError(RuntimeError):
    pass


class Phase(Enum):
    LIST = auto()
    SCROLL = auto()
    OPEN = auto()
    DETAIL = auto()
    ENHANCE = auto()
    CONFIRM = auto()
    RETURN = auto()
    REWARD = auto()
    DONE = auto()
    INVALID = auto()


@dataclass(frozen=True)
class AutoSettings:
    rounds: int
    threshold: float
    confirmation: str = 'auto'
    max_items: int = 1600
    max_scrolls: int = 1200
    max_seconds: float = 7200
    keep_two_elements: bool = True

    def __post_init__(self):
        if self.rounds not in (1, 2, 3) or self.confirmation not in ('auto', 'manual'):
            raise ValueError('自动扫描仅支持强化 1～3 次及自动/手动确认')
        if not math.isfinite(self.threshold) or not 0 < self.threshold <= 100:
            raise ValueError('红色词条阈值无效')
        if (not all(math.isfinite(v) for v in (self.max_items, self.max_scrolls, self.max_seconds))
                or min(self.max_items, self.max_scrolls, self.max_seconds) <= 0):
            raise ValueError('自动扫描安全上限必须为正数')


@dataclass(frozen=True)
class Intent:
    sequence: int
    kind: str
    point: tuple[int, int] | None = None
    wheel: int = 0
    reason: str = ''


@dataclass
class ItemSession:
    serial: int
    initial: tuple[str, ...]
    current: tuple[str, ...]
    origin_filled: int = 2
    completed: int = 0
    opened: bool = False
    approved: bool = False
    confirm_sent: bool = False
    confirm_seen: bool = False
    reward_seen: bool = False
    outcome: str = ''
    identity: object = None


def ordered_slots(detail):
    if detail is None or not detail.right_ready or len(detail.right_slots) != 5:
        return None
    slots = detail.right_slots
    active = tuple(slot.element for slot in slots if slot.active)
    if (any(value is None for value in active) or len(active) != detail.right_filled_count
            or len(active) not in (2, 3, 4, 5)):
        return None
    if tuple(slot.active for slot in slots) != (True,) * len(active) + (False,) * (5-len(active)):
        return None
    if tuple(detail.right_combination) != active:
        return None
    return active


class AutoRun:
    STABLE_SECONDS = .25
    LIST_STABLE_SECONDS = .20
    RESULT_SECONDS = .40
    ENHANCE_STABLE_SECONDS = .18
    # Newly revealed slots animate through colors which can be classified
    # consistently for several frames. Require settled pixels AND colors,
    # with a minimum age after the acknowledged click (never a blind retry).
    SLOT_STABLE_SECONDS = .35
    ENHANCE_MIN_SECONDS = .65
    CONFIRM_STABLE_SECONDS = .18
    REWARD_STABLE_SECONDS = .12
    SCROLL_SETTLE = .30
    # Modestly faster than downward scanning, with observation after every
    # step. Never fire a blind whole-inventory jump at startup or on rescan.
    TOP_SEEK_NOTCHES = 2
    MAX_SCROLL_NOTCHES = 32
    # A short viewport contains fewer than three rows. Two-row travel leaves
    # too little distinctive overlap when neighboring imprints look alike.
    TARGET_SCROLL_PIXELS = 90
    TOP_SCROLL_PIXELS = 90
    TIMEOUT = 8.0
    STILL_ATTEMPTS = 2

    def __init__(self, settings, keep_policy, now, *, identity_roi=None):
        self.settings = settings
        self.keep_policy = keep_policy
        self.started = self.phase_at = now
        self.phase = Phase.LIST
        self.fixed_identity_roi = identity_roi
        self.identity_roi = identity_roi or (310, 261, 490, 301)
        self.identity_reference = None
        self.identity_difference = None
        self.pending = None
        self.sequence = 0
        self.item = None
        self.processed = self.kept = self.decomposed = self.clicks = self.red_matches = 0
        self.scrolls = self.pass_processed = 0
        self.pass_number = 1
        self.candidates = 0
        self.seeking_top = True
        self.top_seek_notches = self.TOP_SEEK_NOTCHES
        self.top_seek_limit = self.MAX_SCROLL_NOTCHES
        self.down_notches = 1
        self.sent_scroll_notches = 1
        self.scroll_pixels_per_notch = {1: 0., -1: 0.}
        self.message = '自动扫描：小幅上滑检查新刻印，到顶后开始扫描'
        self.stable_key = None
        self.stable_since = now
        self.stable_frames = 0
        self.stable_image = None
        self.scroll_before = None
        self.scroll_kind = 'travel'
        self.scroll_direction = 1
        self.still = 0
        self.boundary_image = None
        self.boundary_proven = False
        self.next_scroll_kind = 'travel'
        self.last_decomposed = None
        self.valid = True
        self.single_page = False
        self.short_page_items = self.short_page_decomposed = 0
        self.check_top_crop = False
        self.last_motion = None
        self.last_observation = '尚未收到列表截图'
        self.readable_key = None
        self.readable_frames = 0
        self.readable_since = now
        self.last_elements = None
        self.session_evidence = None
        self.scroll_leg_moved = False
        self.scroll_end_clamped = False
        self.last_boundary = None

    def expects_empty(self):
        return bool(self.single_page and self.item and self.item.outcome == 'decompose'
                    and (self.item.confirm_sent or self.item.reward_seen)
                    and self.short_page_items == self.decomposed-self.short_page_decomposed+1
                    and self.phase in (Phase.RETURN, Phase.REWARD))

    def invalidate(self):
        if self.item is not None:
            s = self.item
            self.session_evidence = {
                'serial': s.serial, 'initial': s.initial, 'expected': s.current,
                'completed': s.completed, 'origin_filled': s.origin_filled,
                'approved': s.approved, 'confirm_sent': s.confirm_sent,
            }
        self.valid = False
        self.pending = None
        self.item = None
        self.phase = Phase.INVALID

    def _fail(self, message):
        self.failed_phase = self.phase
        self.message = message
        self.invalidate()
        raise AutoSafetyError(message)

    def _transition(self, phase, now, *, retain_stability=False):
        self.phase = phase
        self.phase_at = now
        self.pending = None
        if not retain_stability:
            self.stable_key = self.stable_image = None
            self.stable_frames = 0
            self.stable_since = now
            self.readable_key = None
            self.readable_frames = 0
            self.readable_since = now

    def _readable(self, scan, now):
        if scan.uncertain or not scan.cards:
            self.readable_key = None
            self.readable_frames = 0
            return False
        key = scan.stability_key
        if key != self.readable_key:
            self.readable_key, self.readable_frames, self.readable_since = key, 1, now
            return False
        self.readable_frames += 1
        return self.readable_frames >= 3 and now - self.readable_since >= self.LIST_STABLE_SECONDS

    def _stable(self, key, now, image=None, *, result=False, delay=None):
        changed = key != self.stable_key or (image is not None and image_distance(image, self.stable_image) > 1.5)
        if changed:
            self.stable_key = key
            self.stable_image = image.copy() if image is not None else None
            self.stable_since = now
            self.stable_frames = 1
            return False
        self.stable_frames += 1
        if delay is None:
            delay = self.RESULT_SECONDS if result else self.STABLE_SECONDS
        return self.stable_frames >= 3 and now - self.stable_since >= delay

    def _intent(self, kind, *, point=None, wheel=0, reason=''):
        self.sequence += 1
        self.pending = Intent(self.sequence, kind, point, wheel, reason)
        return self.pending

    def allows(self, intent):
        if not self.valid or self.pending is not intent:
            return False
        expected_phase = {'select': Phase.LIST, 'scroll': Phase.LIST,
                          'enhance': Phase.DETAIL, 'keep': Phase.DETAIL,
                          'dismantle': Phase.DETAIL, 'confirm': Phase.CONFIRM,
                          'reward': Phase.RETURN}
        if self.phase != expected_phase.get(intent.kind):
            return False
        if intent.kind == 'scroll':
            if self.scroll_kind == 'top_seek':
                return (self.seeking_top and self.item is None
                        and 1 <= self.top_seek_notches <= self.top_seek_limit <= self.MAX_SCROLL_NOTCHES
                        and intent.wheel == self.top_seek_notches)
            if self.scroll_kind == 'travel':
                return (not self.seeking_top and self.item is None
                        and 1 <= self.down_notches <= self.MAX_SCROLL_NOTCHES
                        and intent.wheel == -self.down_notches)
            if abs(intent.wheel) != 1:
                return False
        if intent.kind in ('enhance', 'keep'):
            s = self.item
            if not (s and s.opened and s.origin_filled == 2
                    and len(s.current) == 2+s.completed and s.current[:2] == s.initial):
                return False
            if intent.kind == 'enhance' and s.completed >= self.settings.rounds:
                return False
            if intent.kind == 'keep' and s.completed != self.settings.rounds:
                return False
        if intent.kind in ('dismantle', 'confirm'):
            s = self.item
            if not (s and s.opened and s.origin_filled == 2 and len(s.initial) == 2
                    and s.completed == self.settings.rounds
                    and len(s.current) == 2 + self.settings.rounds and s.approved):
                return False
            if intent.kind == 'confirm' and (not s.confirm_seen or s.confirm_sent):
                return False
        return True

    def acknowledge(self, intent, now):
        if not self.allows(intent):
            self._fail('动作授权已失效，未继续操作')
        kind = intent.kind
        self.pending = None
        if kind == 'select':
            self._transition(Phase.OPEN, now)
        elif kind == 'enhance':
            self.clicks += 1
            self._transition(Phase.ENHANCE, now)
        elif kind == 'keep':
            self.item.outcome = 'keep'
            self._transition(Phase.RETURN, now)
        elif kind == 'dismantle':
            self.item.outcome = 'decompose'
            self._transition(Phase.CONFIRM, now)
        elif kind == 'confirm':
            self.item.confirm_sent = True
            self._transition(Phase.RETURN, now)
        elif kind == 'reward':
            self._transition(Phase.REWARD, now)
        elif kind == 'scroll':
            self.scrolls += 1
            self.sent_scroll_notches = abs(intent.wheel)
            self._transition(Phase.SCROLL, now)

    def _identity(self, frame):
        x0, y0, x1, y1 = self.identity_roi
        return frame[y0:y1, x0:x1].copy()

    def _validate_identity(self, frame):
        if self.item.identity is not None:
            self.identity_difference = image_distance(self.item.identity, self._identity(frame))
            if self.identity_difference > 14:
                self._fail('当前刻印名称发生变化，处理记录已失效；未分解')

    def _detail(self, analysis, frame, now):
        if analysis.state != ImprintState.DETAIL or not analysis.dismantle_ready:
            self.stable_key = None
            return None
        elements = ordered_slots(analysis.detail)
        self.last_elements = elements
        if elements is None:
            self.stable_key = None
            return None
        slot_y = getattr(analysis.detail, 'right_slot_y', 0) or 355
        slot_image = frame[max(0, slot_y-14):slot_y+15, 300:505]
        # Intermediate red text is still animating and is not used to decide
        # the next enhancement. It must not reset otherwise stable slots.
        final_slots = self.item and len(elements) == 2+self.settings.rounds
        key = (elements, slot_y,
               tuple(analysis.red_attribute_values) if final_slots else (),
               analysis.red_attribute_unreadable_count if final_slots else 0)
        # Observe each slot increase once, then reuse that observation for
        # the next enhancement. Final red-value decisions keep their longer
        # stabilization time and destructive actions still recapture.
        final_decision = self.phase == Phase.DETAIL and self.item and self.item.completed >= self.settings.rounds
        delay = self.RESULT_SECONDS if final_decision else self.ENHANCE_STABLE_SECONDS
        if self.phase == Phase.ENHANCE:
            delay = self.SLOT_STABLE_SECONDS
        if not self._stable(key, now, slot_image, delay=delay):
            return None
        if self.phase == Phase.ENHANCE and now-self.phase_at < self.ENHANCE_MIN_SECONDS:
            return None
        s = self.item
        if not s or s.origin_filled != 2:
            self._fail('缺少本轮初始 2 属性记录，禁止强化和分解')
        if self.phase == Phase.OPEN:
            if len(elements) != 2 or elements != s.initial:
                self._fail('所选刻印详情与初始 2 属性候选不一致；原有 3/4/5 属性不会处理')
            s.opened = True
            if self.fixed_identity_roi is None:
                # The proficiency toast covers the artwork and enhancement
                # changes its level badge. The red imprint name below both
                # remains unchanged. Anchor it to the recognized slot row to
                # tolerate the supported normalized title-bar offsets.
                slot_y = getattr(analysis.detail, 'right_slot_y', 0) or 355
                self.identity_roi = (310, slot_y-94, 490, slot_y-54)
            s.identity = self._identity(frame)
            self.identity_reference = s.identity.copy()
            self.identity_difference = 0.
            self._transition(Phase.DETAIL, now, retain_stability=True)
            # Continue on this verified frame, with a fresh capture in the
            # dispatcher. No extra empty worker tick between ready actions.
        self._validate_identity(frame)
        if self.phase == Phase.ENHANCE:
            if elements == s.current:
                self.message = f'等待强化成功：{s.completed}/{self.settings.rounds}；不会重复点击'
                return None
            if len(elements) != len(s.current) + 1 or elements[:-1] != s.current:
                self._fail('强化槽位没有按顺序增加 1 个，已暂停，未分解')
            s.current = elements
            s.completed += 1
            self._transition(Phase.DETAIL, now, retain_stability=True)
            self.message = f'已确认强化成功 {s.completed}/{self.settings.rounds} 次：{" + ".join(elements)}'
            # Keep the final decision's longer stability gate. Intermediate
            # steps can proceed immediately after the same .65/.35 guards.
            if s.completed >= self.settings.rounds and now-self.stable_since < self.RESULT_SECONDS:
                return None
        if elements != s.current:
            self._fail(f'当前属性与本轮记录不一致，禁止继续操作；'
                       f'预期={" + ".join(s.current)}；实际={" + ".join(elements)}')
        if s.completed < self.settings.rounds:
            self.message = f'强化第 {s.completed+1}/{self.settings.rounds} 次'
            return self._intent('enhance', reason=self.message)
        if analysis.red_attribute_unreadable_count:
            self._fail('红色词条数值无法确认，当前刻印已保护；请人工检查后回列表重新开始')
        values = tuple(analysis.red_attribute_values)
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in values):
            self._fail('红色词条数值异常，当前刻印已保护')
        reason = self.keep_policy(values, 0, self.settings.threshold, s.initial, elements,
                                  keep_two_elements=self.settings.keep_two_elements)
        if reason:
            self.message = f'保留：{reason}；随后继续扫描'
            # Count only after returning to LIST, not when a button is guessed.
            self._current_red_match = any(v >= self.settings.threshold for v in values)
            return self._intent('keep', reason=self.message)
        s.approved = True
        self.message = '本轮初始 2 属性、强化次数及筛选结果已复核，允许分解'
        return self._intent('dismantle', reason=self.message)

    def _request_scroll(self, scan, image, kind):
        if self.scrolls >= self.settings.max_scrolls:
            self._fail('达到滚动安全上限，已暂停；不是全部完成')
        direction = 1 if self.seeking_top else -1
        if self.seeking_top and kind == 'travel':
            kind = 'top_seek'
        if kind == 'probe':
            direction = -direction
        self.scroll_kind = kind
        self.scroll_direction = direction
        self.scroll_before = image.copy()
        x0, y0, x1, y1 = scan.roi
        self.message = ('正在回到顶部' if self.seeking_top else '当前页无候选，小幅下滑')
        if kind == 'top_seek':
            self.message = f'小幅上滑 {self.top_seek_notches} 格，检查是否还有新刻印'
        elif kind == 'travel':
            self.message = f'当前页无候选，下滑 {self.down_notches} 格（按实际位移校准）'
        elif kind != 'travel':
            self.message = '正在往返验证滚轮与列表边界'
        notches = self.top_seek_notches if kind == 'top_seek' else self.down_notches if kind == 'travel' else 1
        return self._intent('scroll', point=((x0+x1)//2, (y0+y1)//2),
                            wheel=notches * direction,
                            reason=self.message)

    def _boundary(self, now, scan):
        if scan.uncertain or not scan.cards:
            self._fail('边界画面已停稳，但槽位仍无法确认；未宣称完成：' + ', '.join(scan.issues))
        self.last_boundary = {
            'edge': 'top' if self.seeking_top else 'bottom',
            'method': 'clamped_motion_then_still' if self.scroll_end_clamped else 'probe_restore',
            'stationary_attempts': self.still, 'scrolls': self.scrolls,
        }
        self.scroll_leg_moved = False
        self.scroll_end_clamped = False
        self.still = 0
        self.boundary_image = None
        self.boundary_proven = False
        self.next_scroll_kind = 'travel'
        if self.seeking_top:
            self.seeking_top = False
            self.check_top_crop = True
            self.message = '已验证顶部，按行从左到右扫描'
        elif scan.clipped_bottom:
            self._fail('已到滚动边界，但底部仍有被遮挡卡片；请检查窗口比例/列表边界，未宣称全部完成')
        elif scan.candidates:
            self.message = '已到达底部，先处理完整可见的初始 2 属性刻印'
        else:
            self._transition(Phase.DONE, now)
            self.message = '已确认到底且当前无候选，本次向下扫描完成；不再回顶部复扫'
            return
        self._transition(Phase.LIST, now, retain_stability=True)

    def _scroll_result(self, scan, image, now):
        difference = image_distance(self.scroll_before, image)
        direction = self.scroll_direction
        self.last_motion = {'kind': self.scroll_kind, 'direction': direction,
                            'notches': self.sent_scroll_notches,
                            'difference': round(difference, 3), 'displacement': None}
        if difference <= 1.5:
            if self.scroll_kind == 'probe':
                if scan.short_page and not scan.uncertain and self.seeking_top:
                    # A sizeable, positively empty parchment band below all
                    # complete cards proves a short single-page inventory.
                    self.single_page = True
                    self.short_page_items = len(scan.cards)
                    self.short_page_decomposed = self.decomposed
                    self.seeking_top = False
                    self.still = 0
                    self.next_scroll_kind = 'travel'
                    self._transition(Phase.LIST, now, retain_stability=True)
                    self.message = '已确认单页小库存，开始逐枚处理'
                    return
                self._fail('正反滚轮都未产生可验证移动，无法确认列表边界；请检查窗口/列表区域')
            if self.scroll_kind == 'restore':
                self._fail('边界往返验证未恢复原位置，已暂停')
            self.still += 1
            if self.still >= self.STILL_ATTEMPTS:
                if self.scroll_leg_moved and self.scroll_end_clamped:
                    # Recently verified motion became shorter than the
                    # measured wheel distance (edge clamping), followed by
                    # two separate stationary attempts. Reuse that evidence.
                    # Ordinary motion alone is NOT enough: a wheel that stops
                    # responding mid-list must still fail the reverse probe.
                    # step() also requires stable readable slots before use.
                    self.boundary_proven = True
                    self.next_scroll_kind = 'travel'
                else:
                    # Startup at an edge / after inventory mutations: never
                    # mistake an unresponsive wheel for a verified boundary.
                    self.boundary_image = image.copy()
                    self.next_scroll_kind = 'probe'
            else:
                self.next_scroll_kind = 'travel'
        else:
            max_shift = scroll_motion_limit(image.shape[0])
            displacement = scroll_displacement(self.scroll_before, image, max_shift)
            self.last_motion['displacement'] = displacement
            if (displacement is None and self.scroll_kind == 'top_seek'
                    and self.top_seek_notches > 1):
                # Some wheel settings move too far even with two notches.
                # Reduce once to a single notch for the rest of this ascent;
                # this unknown movement proves neither direction nor top and
                # never authorizes selection. The next step must be verified.
                self.top_seek_notches = 1
                self.top_seek_limit = 1
                self.scroll_leg_moved = False
                self.scroll_end_clamped = False
                self.still = 0
                self.boundary_proven = False
                self.boundary_image = None
                self.next_scroll_kind = 'travel'
                self._transition(Phase.LIST, now, retain_stability=True)
                self.message = '上滑后重叠区域不足，改为每次一格继续核验'
                return
            if displacement is None or displacement * direction <= 0:
                self._fail('无法验证滚动方向和重叠区域，可能跨过整行；已暂停，未宣称完成')
            if self.scroll_kind == 'probe':
                self.next_scroll_kind = 'restore'
            elif self.scroll_kind == 'restore':
                if image_distance(image, self.boundary_image) > 2.5:
                    self._fail('滚轮往返后未恢复同一列表边界，已暂停')
                self.boundary_proven = True
                # Two stationary attempts + a real inverse displacement +
                # the exact anchor restored already prove the boundary.
                # Do not repeat three more upward/downward wheel commands.
                self.next_scroll_kind = 'travel'
            else:
                self.scroll_leg_moved = True
                # Wheel settings/game sensitivity vary widely: the live game
                # can move only 5-7 px per notch. Calibrate from verified
                # motion, target two rows down / one row up, at most 2x per
                # observed step. Probe/restore remain exact single notches.
                target = min(self.TOP_SCROLL_PIXELS if self.seeking_top else self.TARGET_SCROLL_PIXELS,
                             max(5, min(max_shift-8, image.shape[0] // 3)))
                # A step clamped at an edge can be shorter than normal. Do
                # not learn a falsely slow wheel from it and overshoot on
                # the next pass. Retain the fastest verified rate per direction.
                expected = self.scroll_pixels_per_notch[direction] * self.sent_scroll_notches
                self.scroll_end_clamped = (expected >= 8 and abs(displacement)+3 < expected*.8)
                speed = max(self.scroll_pixels_per_notch[direction],
                            abs(displacement) / self.sent_scroll_notches)
                self.scroll_pixels_per_notch[direction] = speed
                desired = max(1, int(target / speed))
                limit = self.top_seek_limit if self.seeking_top else self.MAX_SCROLL_NOTCHES
                next_notches = min(limit, self.sent_scroll_notches * 2, desired)
                if self.seeking_top:
                    self.top_seek_notches = max(min(2, limit), next_notches)
                else:
                    self.down_notches = next_notches
                self.still = 0
                self.boundary_proven = False
                self.boundary_image = None
                self.next_scroll_kind = 'travel'
        self._transition(Phase.LIST, now, retain_stability=True)

    def _returned(self, now, scan):
        s = self.item
        if s.outcome == 'keep':
            self.kept += 1
            self.red_matches += bool(getattr(self, '_current_red_match', False))
        else:
            self.decomposed += 1
            self.last_decomposed = s.current
        self.processed += 1
        self.pass_processed += 1
        self.item = None
        # Deletion/reordering invalidates earlier scroll-response evidence.
        self.scroll_leg_moved = False
        self.scroll_end_clamped = False
        self.still = 0
        self.boundary_proven = False
        self.boundary_image = None
        self.next_scroll_kind = 'travel'
        self.message = '已返回列表，重新识别补位后的所有坐标'
        # The return gate already verified current list pixels AND slots.
        # Rebase those observations, not any pre-deletion card coordinates.
        self._transition(Phase.LIST, now, retain_stability=True)
        self.stable_key = ('inventory', scan.roi)
        self.readable_key = scan.stability_key
        self.readable_frames = self.stable_frames
        self.readable_since = self.stable_since

    def step(self, analysis, frame, scan, now):
        if not self.valid or self.phase in (Phase.DONE, Phase.INVALID):
            return None
        if now - self.started > self.settings.max_seconds:
            self._fail('达到运行时长安全上限，已暂停；不是全部完成')
        # An unacknowledged intent (e.g. cooldown) must be re-evaluated using
        # this frame, never carried over as a stale click authorization.
        self.pending = None
        timeout = self.TIMEOUT
        if self.phase == Phase.CONFIRM and self.settings.confirmation == 'manual':
            timeout = 300
        if now - self.phase_at > timeout:
            reasons = {
                Phase.LIST: '列表槽位未能完整、稳定识别；未继续下滑或选择',
                Phase.SCROLL: '滚动后画面未停稳或未回到列表；未重复发送滚轮',
                Phase.OPEN: '选择后未观察到可核验详情；未重复点击',
                Phase.ENHANCE: '强化后未观察到槽位增加（可能材料不足）；未重复强化',
                Phase.DETAIL: '详情属性未稳定；未分解',
                Phase.CONFIRM: '未观察到关联分解确认结果；未重复点击',
                Phase.RETURN: '尚未确认返回列表；未重复点击',
                Phase.REWARD: '尚未确认奖励关闭；未重复点击',
            }
            self._fail(f'{self.phase.name} 等待超时：{reasons.get(self.phase, "界面未响应")}。'
                       f'最近识别：{self.last_observation}')
        state = analysis.state
        self.last_observation = (f'{state.name}; 卡片={len(scan.cards)}, '
                                 f'不确定={scan.uncertain}, 问题={scan.issues}'
                                 if scan is not None else f'{state.name}; 无列表识别')
        if self.phase == Phase.DETAIL and state != ImprintState.DETAIL:
            self._fail('稳定详情意外消失，处理记录已撤销；请回列表重新开始')
        if self.phase in (Phase.LIST, Phase.SCROLL):
            if state in (ImprintState.DETAIL, ImprintState.CONFIRM, ImprintState.REWARD):
                self._fail('自动扫描必须从刻印列表开始；不接管未关联的详情或弹窗')
            if state != ImprintState.LIST or scan is None or not scan.cards:
                self.stable_key = None
                self.readable_key = None
                self.message = '等待列表槽位完整识别；不把识别失败当成空列表'
                return None
            image = list_image(frame, scan.roi)
            # Pixel stability proves scrolling has settled. Eligibility is
            # separate: unreadable cards NEVER authorize selection or travel
            # down the inventory. One-pixel coordinate jitter is harmless.
            readable = self._readable(scan, now)
            if not self._stable(('inventory', scan.roi), now, image, delay=self.LIST_STABLE_SECONDS):
                return None
            if self.phase == Phase.SCROLL:
                if now - self.phase_at < self.SCROLL_SETTLE:
                    return None
                self._scroll_result(scan, image, now)
                if self.phase != Phase.LIST:
                    return None
                # Reuse this already settled frame instead of paying for
                # the same three-frame wait again in LIST.
            if not readable:
                self.message = '画面已停稳，等待槽位识别：' + (', '.join(scan.issues) or '等待连续稳定槽位')
                return None
            if self.boundary_proven:
                self._boundary(now, scan)
                if self.phase != Phase.LIST:
                    return None
            self.candidates = len(scan.candidates)
            if self.check_top_crop:
                self.check_top_crop = False
                if any(c.filled == 2 and c.slot_y-c.pitch*5 < scan.roi[1]+2 for c in scan.cards):
                    self._fail('列表顶部仍有被裁切的 2 属性刻印；请校准窗口/列表边界，避免漏选')
            if not self.seeking_top and self.next_scroll_kind == 'travel' and not self.boundary_proven and scan.candidates:
                if self.processed >= self.settings.max_items:
                    self._fail('达到刻印数量安全上限，已暂停；不是全部完成')
                # y jitter of a pixel must not reorder columns within a row.
                top_y = min(c.slot_y for c in scan.candidates)
                c = min((c for c in scan.candidates if abs(c.slot_y-top_y) <= 3), key=lambda c: c.x)
                self.item = ItemSession(self.processed+1, c.elements, c.elements)
                self.message = f'选择当前可见行第 {c.column+1} 列：仅初始 2 属性'
                return self._intent('select', point=(c.x, c.y), reason=self.message)
            if self.single_page and scan.short_page and not self.candidates and not self.seeking_top:
                self._transition(Phase.DONE, now)
                self.message = '已复核单页库存，无剩余初始 2 属性刻印，处理完成'
                return None
            return self._request_scroll(scan, image, self.next_scroll_kind)
        if self.phase in (Phase.OPEN, Phase.DETAIL, Phase.ENHANCE):
            if state in (ImprintState.CONFIRM, ImprintState.REWARD):
                self._fail('强化期间出现未关联弹窗，已撤销分解授权')
            if state == ImprintState.LIST and self.phase != Phase.OPEN:
                self._fail('强化期间意外返回列表，处理记录已失效')
            return self._detail(analysis, frame, now)
        s = self.item
        if s is None or s.origin_filled != 2 or not s.opened:
            self._fail('缺少本轮处理记录，禁止处理确认弹窗')
        if self.phase == Phase.CONFIRM:
            if state == ImprintState.CONFIRM and analysis.confirm_ready:
                if not self._stable(('confirm',), now, frame[370:710, 30:520],
                                    delay=self.CONFIRM_STABLE_SECONDS):
                    return None
                s.confirm_seen = True
                if self.settings.confirmation == 'manual':
                    self.message = '等待你手动确认分解；取消将暂停自动扫描'
                    return None
                return self._intent('confirm', reason='再次核验本轮初始 2 属性分解授权')
            if s.confirm_seen and state == ImprintState.DETAIL:
                self._fail('分解确认已取消，当前刻印保留；请回列表重新开始')
            if self.settings.confirmation == 'manual' and s.confirm_seen and state == ImprintState.REWARD:
                s.reward_seen = True
                self._transition(Phase.RETURN, now)
                return None
            if state == ImprintState.LIST:
                self._fail('未观察到关联分解确认/奖励，无法确认结果；已暂停')
            self.stable_key = None
            return None
        if self.phase in (Phase.RETURN, Phase.REWARD):
            if state == ImprintState.CONFIRM:
                self.stable_key = None
                return None  # Never send a second confirmation.
            if state == ImprintState.REWARD and s.outcome == 'decompose' and analysis.reward_ready:
                if self.phase == Phase.RETURN and self._stable(
                        ('reward',), now, frame[440:610, :550], delay=self.REWARD_STABLE_SECONDS):
                    s.reward_seen = True
                    return self._intent('reward', reason='关闭本轮分解奖励，返回列表')
                return None
            if state == ImprintState.LIST:
                if s.outcome == 'decompose' and not (s.confirm_sent or s.reward_seen):
                    self._fail('分解完成证据不足，未计入成功数量')
                empty = bool(scan and scan.empty and self.expects_empty())
                if scan is None or scan.uncertain or (not scan.cards and not empty):
                    self.stable_key = None
                    return None
                if self._stable(('returned', scan.stability_key), now, list_image(frame, scan.roi),
                                delay=self.LIST_STABLE_SECONDS):
                    if s.outcome == 'decompose' and not s.reward_seen and now-self.phase_at < 2.5:
                        return None
                    self._returned(now, scan)
                    if empty:
                        self._transition(Phase.DONE, now)
                        self.message = '已确认单页库存最后一枚分解完成，列表为空'
                    else:
                        # Same-frame planning, fresh pre-click capture in the
                        # adapter. No second three-frame wait after return.
                        return self.step(analysis, frame, scan, now)
                return None
            self.stable_key = None
        return None
