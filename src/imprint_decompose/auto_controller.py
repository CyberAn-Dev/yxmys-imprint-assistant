"""Adapter between the offline automatic workflow and existing game I/O."""
import time

from tower_bot.models import ActionType, BotError, Point, Rect
from tower_bot.logger import get_logger

from .auto_mode import AutoRun, AutoSettings, AutoSafetyError, Phase, ordered_slots
from .auto_vision import ListScan, blank_inventory, image_distance, list_chrome, read_inventory
from .models import ImprintState, combination_label

logger = get_logger(__name__)


class AutoControllerMixin:
    def __init__(self, *args, **kwargs):
        self._scan_mode_enabled = False
        self._scan_run = None
        self._scan_window = None
        self._scan_chrome = None
        self._scan_intent = None
        self._scan_base_counts = (0, 0, 0)
        self._scan_reported_decomposed = 0
        super().__init__(*args, **kwargs)
        original_guard = self.input._guard_dispatch

        def guarded_dispatch(window, rect, started):
            original_guard(window, rect, started)
            if self._scan_mode_enabled:
                run, intent = self._scan_run, self._scan_intent
                if not (run and intent and run.allows(intent) and self.capture.is_fresh()):
                    raise BotError('自动扫描动作授权或截图已失效，取消输入')
        self.input._guard_dispatch = guarded_dispatch

    def _invalidate_scan(self):
        run = self._scan_run
        if run:
            run.invalidate()
        self._scan_intent = None

    def set_auto_mode(self, enabled):
        enabled = bool(enabled)
        if self.enabled.is_set():
            self.stop()
        with self._auto_settings_lock:
            self._invalidate_scan()
            self._scan_mode_enabled = enabled
            # Keep the unsafe legacy automatic traversal permanently disabled.
            self._auto_enabled = False
            self.stats.auto_mode = enabled
            self.stats.auto_filter = '仅本轮初始 2 属性；原有 3/4/5 跳过'
            if enabled:
                self.set_enhancement_settings(True, self._enhancement_target, self._enhancement_red_threshold)
            self.stats.auto_phase = '待开始' if enabled else '手动选卡'
            self.stats.last_action = '请回到刻印列表，F9 开始自动扫描' if enabled else '等待你手动选择刻印'
        self._emit_stats()

    def set_enhancement_settings(self, enabled, rounds, red_threshold_percent):
        if self._scan_mode_enabled and not enabled:
            raise ValueError('自动扫描必须开启强化；如需关闭，请先切换为手动选卡')
        if self.enabled.is_set():
            self.pause()
            self.stats.last_action = '设置已变更并暂停；请返回列表后重新开始'
        return super().set_enhancement_settings(enabled, rounds, red_threshold_percent)

    def set_confirmation_mode(self, mode):
        if self.enabled.is_set():
            self.pause()
            self.stats.last_action = '确认方式变更，已暂停；请返回列表后重新开始'
        return super().set_confirmation_mode(mode)

    def start(self):
        if self.enabled.is_set() or self.stopping.is_set():
            return
        with self._auto_settings_lock:
            # Restart never inherits an old detail/confirmation authorization.
            super().stop()
            self._invalidate_scan()
            if self._scan_mode_enabled:
                if not self._enhancement_enabled:
                    self.stats.last_error = '自动扫描必须开启强化'
                    self._emit_stats()
                    return
                cfg = self.feature_cfg.get('automatic', {})
                settings = AutoSettings(
                    self._enhancement_target, self._enhancement_red_threshold,
                    self._confirmation_mode,
                    int(cfg.get('max_items', 1600)), int(cfg.get('max_scrolls', 1200)),
                    float(cfg.get('max_seconds', 7200)),
                )
                self._scan_run = AutoRun(settings, self._auto_keep_policy, time.monotonic())
                self._scan_window = None
                self._scan_chrome = None
                self._scan_base_counts = (self.stats.total_kept, self.stats.red_threshold_matches,
                                          self.stats.enhancement_clicks)
                self._scan_reported_decomposed = 0
                self.stats.auto_processed = self.stats.auto_scrolled = self.stats.auto_candidates = 0
            super().start()

    def pause(self):
        # Clear the native-input gate before taking any lock or emitting stats.
        self.enabled.clear()
        self._invalidate_scan()
        self._pending_combination = None
        self._phase = 'IDLE'
        self._reset_enhancement_session()
        super().pause()
        if self._scan_mode_enabled:
            self.stats.auto_phase = '已暂停'
            self.stats.last_action = '处理记录已撤销；回到列表后按 F9 重新扫描'
            self._emit_stats()

    def stop(self):
        self.enabled.clear()
        self._invalidate_scan()
        super().stop()
        if self._scan_mode_enabled:
            self.stats.auto_phase = '已停止'

    def emergency_pause(self, reason='Esc 紧急暂停'):
        self.enabled.clear()
        self._invalidate_scan()
        self._pending_combination = None
        self._phase = 'IDLE'
        self._reset_enhancement_session()
        super().emergency_pause(reason)

    def shutdown(self):
        self.enabled.clear()
        self._invalidate_scan()
        return super().shutdown()

    def _sync_scan_stats(self, run):
        self.stats.auto_mode = self._scan_mode_enabled
        self.stats.auto_candidates = run.candidates
        self.stats.auto_scrolled = run.scrolls
        self.stats.auto_processed = run.processed
        self.stats.auto_pass = run.pass_number
        names = {
            Phase.LIST: '回到顶部' if run.seeking_top else '逐卡扫描',
            Phase.SCROLL: '滚动验证', Phase.OPEN: '复核初始属性',
            Phase.DETAIL: '筛选判断', Phase.ENHANCE: '等待强化',
            Phase.CONFIRM: '等待分解确认', Phase.RETURN: '等待返回列表',
            Phase.REWARD: '关闭奖励', Phase.DONE: '全部完成', Phase.INVALID: '保护暂停',
        }
        self.stats.auto_phase = names[run.phase]
        self.stats.last_action = run.message
        kept, red, clicks = self._scan_base_counts
        self.stats.total_kept = kept + run.kept
        self.stats.red_threshold_matches = red + run.red_matches
        self.stats.enhancement_clicks = clicks + run.clicks
        self._enhancement_clicks_total = self.stats.enhancement_clicks
        if run.item:
            self.stats.current_combination = combination_label(run.item.current)
            self.stats.current_filled_slots = f'{len(run.item.current)}/5'
            self.stats.enhancement_completed = run.item.completed
        if run.decomposed > self._scan_reported_decomposed:
            self._pending_combination = run.last_decomposed
            self._commit_pending(simulated=False)
            self._scan_reported_decomposed = run.decomposed
        self._emit_stats()

    def _tick(self):
        if not self._scan_mode_enabled:
            return super()._tick()
        if self.stopping.is_set() or not self.enabled.is_set():
            return
        run = self._scan_run
        if not run or not run.valid:
            raise BotError('自动处理记录已失效，请返回列表重新开始')
        frame = analysis = None
        try:
            window = self.locator.find()
            if window is None:
                raise AutoSafetyError('找不到游戏窗口，已暂停自动扫描')
            self.locator.validate_geometry(window)
            signature = (window.hwnd, window.rect)
            if self._scan_window is None:
                # Focus only once, before the first capture. Later loss of
                # focus is an interruption, not permission to steal it back.
                if not self.dry_run and not self.locator.focus(window.hwnd):
                    raise AutoSafetyError('无法激活游戏窗口，已暂停')
                self._scan_window = signature
            if signature != self._scan_window:
                raise AutoSafetyError('游戏窗口移动、缩放或切换，已撤销处理记录')
            if not self.dry_run and not self.locator.is_foreground(window.hwnd):
                raise AutoSafetyError('游戏窗口失去焦点，已暂停；回列表后重新开始')
            self._last_window = window
            frame = self.capture.grab_window(window.rect)
            analysis = self.detector.analyze(frame)
            roi = tuple(self.feature_cfg['vision']['list_roi'])
            empty = (analysis.state == ImprintState.UNKNOWN and run.expects_empty()
                     and self._scan_chrome is not None and blank_inventory(frame, roi)
                     and image_distance(self._scan_chrome, list_chrome(frame, roi)) <= 1.5)
            if empty:
                # The old detector cannot label an empty inventory. This
                # exception can only complete a verified last-item removal;
                # it never authorizes another input.
                analysis.state = ImprintState.LIST
            elif analysis.state == ImprintState.LIST and self._scan_chrome is None:
                self._scan_chrome = list_chrome(frame, roi)
            self._last_analysis = analysis
            self._update_stats(analysis, window)
            scan = (ListScan((), roi, empty=True) if empty else
                    read_inventory(frame, roi) if analysis.state == ImprintState.LIST else None)
            if self.debug_mode.is_set():
                # Legacy writer overwrites this tag; other tags also create
                # timestamped files, which must not accumulate every frame.
                self._save_debug(frame, analysis, tag='latest')
            intent = run.step(analysis, frame, scan, time.monotonic())
            self._sync_scan_stats(run)
            if run.phase == Phase.DONE:
                self.enabled.clear()
                self.stats.program_status = '自动处理完成'
                self._record_operation(run.message)
                self._emit_stats()
                return
            if intent:
                self._dispatch_scan(run, intent, window, analysis)
                self._sync_scan_stats(run)
        except Exception as exc:
            message = str(exc)
            run.message = message
            self.enabled.clear()
            self._invalidate_scan()
            self.stats.auto_phase = '保护暂停'
            self.stats.last_action = message
            self.stats.last_error = message
            if frame is not None and analysis is not None:
                try:
                    self._save_debug(frame, analysis, tag='auto_safety_stop')
                except Exception as debug_exc:
                    # Diagnostic failures must neither hide the original
                    # cause nor defeat the input stop.
                    logger.warning('保存自动扫描诊断图失败: %s', debug_exc)
            self._record_operation(f'AUTO_SAFETY_STOP {message}')
            self._emit_stats()
            raise BotError(message) from exc

    def _dispatch_scan(self, run, intent, window, analysis):
        # Mode/settings/start changes share this lock. Pause/Esc still clear
        # the input event *before* waiting for it. A rapid stop→manual→start
        # must never let an old auto action borrow the new enabled event.
        with self._auto_settings_lock:
            if not self._scan_mode_enabled:
                raise AutoSafetyError('选卡模式已改变，取消旧自动动作')
            return self._dispatch_scan_locked(run, intent, window, analysis)

    def _dispatch_scan_locked(self, run, intent, window, analysis):
        kinds = {
            'select': ActionType.CLICK_IMPRINT_CARD,
            'enhance': ActionType.CLICK_ENHANCE_IMPRINT,
            'keep': ActionType.CLICK_CLOSE_IMPRINT_DETAIL,
            'dismantle': ActionType.CLICK_DISMANTLE,
            'confirm': ActionType.CLICK_CONFIRM_DISMANTLE,
            'reward': ActionType.CLICK_CLOSE_REWARD,
            'scroll': ActionType.SCROLL_IMPRINT_LIST,
        }
        action = kinds[intent.kind]
        if not self.input.can_act(action):
            return
        if not self.enabled.is_set() or run is not self._scan_run or not run.allows(intent):
            raise AutoSafetyError('暂停或设置变更使动作授权失效')
        if not self.dry_run and not self.locator.is_foreground(window.hwnd):
            raise AutoSafetyError('发送输入前游戏窗口失去焦点，取消操作')
        # Destructive actions get a second *new capture* immediately before
        # dispatch. The state machine already required three stable readings.
        if intent.kind in ('select', 'enhance', 'dismantle', 'confirm'):
            latest_frame = self.capture.grab_window(window.rect)
            latest = self.detector.analyze(latest_frame)
            if not self.enabled.is_set() or run is not self._scan_run or not run.allows(intent):
                raise AutoSafetyError('复核期间动作授权已撤销，未发送输入')
            if intent.kind == 'select':
                if latest.state != ImprintState.LIST:
                    raise AutoSafetyError('选卡前列表状态改变，取消点击')
                scan = read_inventory(latest_frame, self.feature_cfg['vision']['list_roi'])
                if scan.uncertain or not any(
                        abs(c.x-intent.point[0]) <= 2 and abs(c.y-intent.point[1]) <= 2
                        and c.elements == run.item.initial for c in scan.candidates):
                    raise AutoSafetyError('选卡前位置或初始属性改变，取消点击')
            elif intent.kind in ('enhance', 'dismantle'):
                if (latest.state != ImprintState.DETAIL or not latest.dismantle_ready
                        or ordered_slots(latest.detail) != run.item.current):
                    raise AutoSafetyError('动作前详情属性发生变化，已保护当前刻印')
                run._validate_identity(latest_frame)
                if intent.kind == 'dismantle' and (
                        latest.red_attribute_unreadable_count
                        or tuple(latest.red_attribute_values) != tuple(analysis.red_attribute_values)
                        or self._auto_keep_policy(latest.red_attribute_values, 0, run.settings.threshold,
                                                  run.item.initial, run.item.current)):
                    raise AutoSafetyError('分解前复核不一致，已保护当前刻印')
            elif latest.state != ImprintState.CONFIRM or not latest.confirm_ready:
                raise AutoSafetyError('确认分解前弹窗状态改变，取消确认')
            analysis = latest
        self._scan_intent = intent
        actions = self.feature_cfg['actions']
        try:
            if intent.kind == 'scroll':
                self.input.scroll_reference(window, Point(*intent.point), clicks=intent.wheel,
                    action=action, reason=intent.reason, expected_hwnd=window.hwnd,
                    frame_fresh=self.capture.is_fresh())
            else:
                if intent.kind == 'select':
                    point = Point(*intent.point)
                    bounds = Rect(point.x-8, point.y-8, point.x+9, point.y+9)
                else:
                    prefix = {'keep': 'detail_close', 'reward': 'reward_close'}.get(intent.kind, intent.kind)
                    point = Point(*actions[prefix+'_point'])
                    bounds = Rect(*actions[prefix+'_bounds'])
                allowed_state = {'select': ImprintState.LIST, 'confirm': ImprintState.CONFIRM,
                                 'reward': ImprintState.REWARD}.get(intent.kind, ImprintState.DETAIL)
                self.input.click_reference(window, point, action=action, reason=intent.reason,
                    click_bounds=bounds, expected_hwnd=window.hwnd, frame_fresh=self.capture.is_fresh(),
                    state_allows=analysis.state == allowed_state, confidence_ok=run.allows(intent),
                    allow_jitter=False)
            if self.dry_run:
                # Do not pretend a simulated click changed the real game.
                self.enabled.clear()
                run.message = f'只读演练：已记录 {intent.kind}，未发送真实输入；请用截图测试继续验证'
                self._invalidate_scan()
                self.stats.program_status = '只读演练已停止'
                self._record_operation(run.message)
                return
            run.acknowledge(intent, time.monotonic())
            self._record_operation(f'AUTO_{intent.kind.upper()} {intent.reason}')
        finally:
            self._scan_intent = None
