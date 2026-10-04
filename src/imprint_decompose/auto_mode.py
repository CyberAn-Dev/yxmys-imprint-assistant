"""Fail-closed inventory workflow. No screen capture, input, threads or UI.

The adapter executes an Intent only after checking its generation, focus,
freshness and window geometry. State advances only on dispatch acknowledgement
and subsequent *observed* game transitions, never on a guessed click count.
"""
from dataclasses import dataclass
from enum import Enum, auto
import math

from .auto_vision import image_distance, list_image, scroll_displacement
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
    RESULT_SECONDS = .40
    SCROLL_SETTLE = .70
    TIMEOUT = 8.0
    STILL_ATTEMPTS = 3

    def __init__(self, settings, keep_policy, now, *, identity_roi=(345, 150, 465, 250)):
        self.settings = settings
        self.keep_policy = keep_policy
        self.started = self.phase_at = now
        self.phase = Phase.LIST
        self.identity_roi = identity_roi
        self.pending = None
        self.sequence = 0
        self.item = None
        self.processed = self.kept = self.decomposed = self.clicks = self.red_matches = 0
        self.scrolls = self.pass_processed = 0
        self.pass_number = 1
        self.candidates = 0
        self.seeking_top = True
        self.message = '自动扫描：先确认列表顶部'
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

    def expects_empty(self):
        return bool(self.single_page and self.item and self.item.outcome == 'decompose'
                    and (self.item.confirm_sent or self.item.reward_seen)
                    and self.short_page_items == self.decomposed-self.short_page_decomposed+1
                    and self.phase in (Phase.RETURN, Phase.REWARD))

    def invalidate(self):
        self.valid = False
        self.pending = None
        self.item = None
        self.phase = Phase.INVALID

    def _fail(self, message):
        self.message = message
        self.invalidate()
        raise AutoSafetyError(message)

    def _transition(self, phase, now):
        self.phase = phase
        self.phase_at = now
        self.pending = None
        self.stable_key = self.stable_image = None
        self.stable_frames = 0
        self.stable_since = now

    def _stable(self, key, now, image=None, *, result=False):
        changed = key != self.stable_key or (image is not None and image_distance(image, self.stable_image) > 1.5)
        if changed:
            self.stable_key = key
            self.stable_image = image.copy() if image is not None else None
            self.stable_since = now
            self.stable_frames = 1
            return False
        self.stable_frames += 1
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
            self._transition(Phase.SCROLL, now)

    def _identity(self, frame):
        x0, y0, x1, y1 = self.identity_roi
        return frame[y0:y1, x0:x1].copy()

    def _validate_identity(self, frame):
        if self.item.identity is not None and image_distance(self.item.identity, self._identity(frame)) > 14:
            self._fail('当前刻印图案发生变化，处理记录已失效；未分解')

    def _detail(self, analysis, frame, now):
        if analysis.state != ImprintState.DETAIL or not analysis.dismantle_ready:
            self.stable_key = None
            return None
        elements = ordered_slots(analysis.detail)
        if elements is None:
            self.stable_key = None
            return None
        key = (elements, tuple(analysis.red_attribute_values), analysis.red_attribute_unreadable_count)
        if not self._stable(key, now, result=True):
            return None
        s = self.item
        if not s or s.origin_filled != 2:
            self._fail('缺少本轮初始 2 属性记录，禁止强化和分解')
        if self.phase == Phase.OPEN:
            if len(elements) != 2 or elements != s.initial:
                self._fail('选卡详情与初始 2 属性候选不一致；原有 3/4/5 属性不会处理')
            s.opened = True
            s.identity = self._identity(frame)
            self._transition(Phase.DETAIL, now)
            return None
        self._validate_identity(frame)
        if self.phase == Phase.ENHANCE:
            if elements == s.current:
                self.message = f'等待强化成功：{s.completed}/{self.settings.rounds}；不会重复点击'
                return None
            if len(elements) != len(s.current) + 1 or elements[:-1] != s.current:
                self._fail('强化槽位没有按顺序增加 1 个，已暂停，未分解')
            s.current = elements
            s.completed += 1
            self._transition(Phase.DETAIL, now)
            self.message = f'已确认强化成功 {s.completed}/{self.settings.rounds} 次'
            return None
        if elements != s.current:
            self._fail('当前属性与本轮记录不一致，禁止继续操作')
        if s.completed < self.settings.rounds:
            self.message = f'强化第 {s.completed+1}/{self.settings.rounds} 次'
            return self._intent('enhance', reason=self.message)
        if analysis.red_attribute_unreadable_count:
            self._fail('红色词条数值无法确认，当前刻印已保护；请人工检查后回列表重新开始')
        values = tuple(analysis.red_attribute_values)
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in values):
            self._fail('红色词条数值异常，当前刻印已保护')
        reason = self.keep_policy(values, 0, self.settings.threshold, s.initial, elements)
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
        if kind == 'probe':
            direction = -direction
        self.scroll_kind = kind
        self.scroll_direction = direction
        self.scroll_before = image.copy()
        x0, y0, x1, y1 = scan.roi
        self.message = ('正在回到顶部' if self.seeking_top else '当前页无候选，小幅下滑')
        if kind != 'travel':
            self.message = '正在往返验证滚轮与列表边界'
        return self._intent('scroll', point=((x0+x1)//2, (y0+y1)//2),
                            wheel=direction, reason=self.message)

    def _boundary(self, now, scan):
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
        elif self.pass_processed:
            # A complete no-work sweep is required after mutations. This
            # catches cards shifted above the viewport by sorting/deletions.
            self.pass_number += 1
            self.pass_processed = 0
            self.seeking_top = True
            self.message = '已到达底部，回顶部复扫以检查补位或重排遗漏'
        else:
            self._transition(Phase.DONE, now)
            self.message = '已验证到底且完整复扫无候选，自动处理完成'
            return
        self._transition(Phase.LIST, now)

    def _scroll_result(self, scan, image, now):
        difference = image_distance(self.scroll_before, image)
        direction = self.scroll_direction
        if difference <= 1.5:
            if self.scroll_kind == 'probe':
                if scan.short_page and self.seeking_top:
                    # A sizeable, positively empty parchment band below all
                    # complete cards proves a short single-page inventory.
                    self.single_page = True
                    self.short_page_items = len(scan.cards)
                    self.short_page_decomposed = self.decomposed
                    self.seeking_top = False
                    self.still = 0
                    self.next_scroll_kind = 'travel'
                    self._transition(Phase.LIST, now)
                    self.message = '已确认单页小库存，开始逐卡处理'
                    return
                self._fail('正反滚轮都未产生可验证移动，无法确认列表边界；请检查窗口/列表区域')
            if self.scroll_kind == 'restore':
                self._fail('边界往返验证未恢复原位置，已暂停')
            self.still += 1
            if self.still >= self.STILL_ATTEMPTS:
                if self.boundary_proven:
                    self._boundary(now, scan)
                    return
                self.boundary_image = image.copy()
                self.next_scroll_kind = 'probe'
            else:
                self.next_scroll_kind = 'travel'
        else:
            max_shift = max(1, image.shape[0] - 100)
            displacement = scroll_displacement(self.scroll_before, image, max_shift)
            if displacement is None or displacement * direction <= 0:
                self._fail('无法验证滚动方向和重叠区域，可能跨过整行；已暂停，未宣称完成')
            if self.scroll_kind == 'probe':
                self.next_scroll_kind = 'restore'
            elif self.scroll_kind == 'restore':
                if image_distance(image, self.boundary_image) > 2.5:
                    self._fail('滚轮往返后未恢复同一列表边界，已暂停')
                self.boundary_proven = True
                self.still = 0
                self.next_scroll_kind = 'travel'
            else:
                self.still = 0
                self.boundary_proven = False
                self.boundary_image = None
                self.next_scroll_kind = 'travel'
        self._transition(Phase.LIST, now)

    def _returned(self, now):
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
        self.still = 0
        self.boundary_proven = False
        self.boundary_image = None
        self.next_scroll_kind = 'travel'
        self.message = '已返回列表，重新识别补位后的所有坐标'
        self._transition(Phase.LIST, now)

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
            self._fail(f'{self.phase.name} 阶段等待超时（可能材料不足或界面未响应），未重复点击')
        state = analysis.state
        if self.phase == Phase.DETAIL and state != ImprintState.DETAIL:
            self._fail('稳定详情意外消失，处理记录已撤销；请回列表重新开始')
        if self.phase in (Phase.LIST, Phase.SCROLL):
            if state in (ImprintState.DETAIL, ImprintState.CONFIRM, ImprintState.REWARD):
                self._fail('自动扫描必须从刻印列表开始；不接管未关联的详情或弹窗')
            if state != ImprintState.LIST or scan is None or scan.uncertain or not scan.cards:
                self.stable_key = None
                self.message = '等待列表槽位完整识别；不把识别失败当成空列表'
                return None
            image = list_image(frame, scan.roi)
            if not self._stable(scan.signature, now, image):
                return None
            if self.phase == Phase.SCROLL:
                if now - self.phase_at < self.SCROLL_SETTLE:
                    return None
                self._scroll_result(scan, image, now)
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
                if not self._stable(('confirm',), now, result=True):
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
            return None
        if self.phase in (Phase.RETURN, Phase.REWARD):
            if state == ImprintState.CONFIRM:
                return None  # Never send a second confirmation.
            if state == ImprintState.REWARD and s.outcome == 'decompose' and analysis.reward_ready:
                if self.phase == Phase.RETURN and self._stable(('reward',), now):
                    s.reward_seen = True
                    return self._intent('reward', reason='关闭本轮分解奖励，返回列表')
                return None
            if state == ImprintState.LIST:
                if s.outcome == 'decompose' and not (s.confirm_sent or s.reward_seen):
                    self._fail('分解完成证据不足，未计入成功数量')
                empty = bool(scan and scan.empty and self.expects_empty())
                if scan is None or scan.uncertain or (not scan.cards and not empty):
                    return None
                if self._stable(('returned', scan.signature), now, list_image(frame, scan.roi)):
                    if s.outcome == 'decompose' and not s.reward_seen and now-self.phase_at < 2.5:
                        return None
                    self._returned(now)
                    if empty:
                        self._transition(Phase.DONE, now)
                        self.message = '已确认单页库存最后一枚分解完成，列表为空'
                return None
            self.stable_key = None
        return None
