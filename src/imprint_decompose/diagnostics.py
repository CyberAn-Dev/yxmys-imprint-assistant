"""Explicit offline package smoke check: no hooks and no native input."""
import json
import traceback
from pathlib import Path


def run(destination):
    result = {'success': False}
    controller = ui = None
    try:
        import numpy as np
        from . import __version__
        from .config import load_feature_config
        from .controller_v2 import ImprintDecomposeController, red_attribute_meets_threshold
        from .digit_ocr_fallback import RedPercentageOCR
        from .ui_v2 import ImprintDecomposeUI, SegmentedControl
        cfg, feature = load_feature_config()
        controller = ImprintDecomposeController(cfg, feature, dry_run=True)
        controller.locator.find = lambda: None
        ui = ImprintDecomposeUI(controller)
        ui.root.update()
        assert not controller.enabled.is_set()
        assert controller._scan_run is None, 'startup must not begin a scan'
        assert controller.stats.auto_mode and controller.stats.enhancement_enabled
        assert controller.stats.enhancement_target == 2
        assert controller.stats.red_attribute_threshold == 20
        assert ui._selection_mode_var.get() == 'auto'
        assert ui._enhancement_enabled_var.get()
        assert ui._enhancement_rounds_var.get() == '2'
        assert ui._enhancement_threshold_var.get() == '20'
        assert ui._keep_two_elements_var.get() and controller.stats.keep_two_elements
        assert ui._startup_hidden, 'window must remain unmapped until final layout'
        assert ui._color_keep_switch.master.master is ui._filter_panel
        assert ui._error_hint.master is ui._outer, 'log hint must be outside modules'
        coffee_center = ui._coffee_link.winfo_x() + ui._coffee_link.winfo_width()/2
        assert abs(coffee_center-ui._footer.winfo_width()/2) <= 1, 'coffee link must be centered'
        from types import SimpleNamespace
        assert '如果你觉得这个工具不错，可以请我喝一杯咖啡' in ui._coffee_link.cget('text')
        assert ui._statistics_panel.winfo_width() > ui._current_panel.winfo_width()*1.4
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        segments = [widget for widget in descendants(ui.root) if isinstance(widget, SegmentedControl)]
        assert len(segments) == 6, 'all six choice groups must share one control style'
        aligned = [ui._selection_switch, ui._confirmation_switch,
                   ui._enhancement_switch, ui._rounds_switch]
        assert len({widget.winfo_rootx() for widget in aligned}) == 1, 'left choices must align'
        assert {widget.winfo_width() for widget in aligned} == {158}, 'left choices must have equal widths'
        resolution_labels = ui._resolution_panel.winfo_children()
        assert len({widget.winfo_rooty() for widget in resolution_labels[1:]}) == 1, 'resolutions and usability must share one row'
        assert ui._usage_status_label.master is ui._resolution_panel
        visible_labels = [widget.cget('text') for widget in descendants(ui.root)
                          if widget.winfo_class() == 'Label']
        assert not any('F11' in text for text in visible_labels), 'debug shortcut must not clutter toolbar'
        assert not any(str(widget.cget('textvariable')) == str(ui._threshold_note_var)
                       for widget in descendants(ui.root) if widget.winfo_class() == 'Label'), 'redundant filter note must be hidden'
        for segment in segments:
            assert segment._control_height == 36
            for item in segment.find_all():
                if segment.type(item) == 'text':
                    left, top, right, bottom = segment.bbox(item)
                    assert 0 <= left < right <= segment.winfo_width(), 'choice text clipped'
                    assert 0 <= top < bottom <= segment.winfo_height(), 'choice text clipped'
            callbacks = []
            command = segment.command
            value = segment.variable.get()
            segment.command = lambda: callbacks.append(True)
            try:
                for index, (_, choice) in enumerate(segment.choices):
                    event = SimpleNamespace(x=(index+0.5)*segment._control_width/len(segment.choices))
                    segment._toggle(event)
                    assert segment.variable.get() == choice
                    count = len(callbacks)
                    segment._toggle(event)
                    assert len(callbacks) == count, 'selected choice must be idempotent'
            finally:
                segment.variable.set(value)
                segment.command = command
        ui._color_keep_switch._toggle(SimpleNamespace(x=100))
        assert not controller.stats.keep_two_elements
        assert '元素保留已关闭' in ui._threshold_note_var.get()
        ui._color_keep_switch._toggle(SimpleNamespace(x=5))
        ui._color_keep_switch._toggle(SimpleNamespace(x=5))
        assert ui._keep_two_elements_var.get(), 'clicking ON twice must not switch OFF'
        assert controller.stats.keep_two_elements and not controller.enabled.is_set()
        assert red_attribute_meets_threshold((20,), 20)
        ocr = RedPercentageOCR(feature)
        assert ocr.available and ocr._ensure_loaded(), 'OCR model unavailable'
        text, confidence = ocr._infer_text(np.full((24, 90, 3), 255, np.uint8))
        assert isinstance(text, str) and np.isfinite(confidence)
        ui._enhancement_threshold_var.set('27')
        ui._on_enhancement_settings_changed()
        ui.root.update()
        assert '≥' in ui._threshold_note_var.get()
        ui._selection_mode_var.set('manual')
        ui._on_selection_mode_changed()
        assert not controller.stats.auto_mode and not controller.enabled.is_set()
        ui._selection_mode_var.set('auto')
        ui._on_selection_mode_changed()
        ui.root.update()
        assert controller.stats.auto_mode and controller.stats.enhancement_enabled
        assert not controller._auto_enabled, 'legacy traversal must stay disabled'
        assert not controller.enabled.is_set(), 'changing mode must not start automation'
        # Replay the real legacy refresh, not a mocked stats updater. Inspect
        # EVERY callback: a latest-only queue can hide intermediate bad modes.
        from .models import ImprintFrameAnalysis, ImprintState
        from tower_bot.models import Rect, WindowInfo
        analysis = ImprintFrameAnalysis(ImprintState.LIST, None, .1, 0, 1, 15,
                                        False, False, 'offline package refresh')
        window = WindowInfo(123, 'offline self-test', Rect(0, 0, 550, 1020))
        callback = controller.on_stats
        snapshots, repaint = [], []
        controller.on_stats = snapshots.append
        trace = ui._selection_mode_var.trace_add('write', lambda *_: repaint.append(ui._selection_mode_var.get()))
        try:
            for _ in range(12):
                controller._update_stats(analysis, window)
                controller.refresh_window_info()  # locator returns None, never queries the game
            assert snapshots and all(s.auto_mode for s in snapshots), 'mode flipped during refresh'
            for snapshot in snapshots:
                ui._apply_stats(snapshot)
                assert ui._selection_mode_var.get() == 'auto'
            assert not repaint, 'unchanged mode control was unnecessarily repainted'
        finally:
            ui._selection_mode_var.trace_remove('write', trace)
            controller.on_stats = callback
        import copy
        fixed_widgets = (ui.root, ui._current_panel, ui._statistics_panel, ui._state_panel)
        baseline_sizes = [(w.winfo_width(), w.winfo_height()) for w in fixed_widgets]
        sample = copy.deepcopy(controller.stats)
        sample.current_combination = '风暴 + 烈焰 + 暗影'
        sample.total_kept = 1
        sample.auto_phase = '保护暂停'
        sample.auto_processed = 1600
        sample.auto_scrolled = 1200
        sample.auto_pass = 12
        sample.last_action = '滚轮往返后未恢复同一列表边界，已暂停；请回到刻印列表后重新开始'
        ui._apply_stats(sample)
        ui.root.update()
        assert [(w.winfo_width(), w.winfo_height()) for w in fixed_widgets] == baseline_sizes, 'status changed panel sizes'
        # Exercise blank/5-slot items, multi-digit counters and long error
        # summaries: none may resize the window or clip any panel's children.
        for combination in ('-', '风暴 + 烈焰 + 电弧 + 暗影 + 大地'):
            sample.current_combination = combination
            sample.element_counts = dict.fromkeys(sample.element_counts, 8000)
            sample.combination_counts = {'regression': 1600}
            sample.last_action = 'AUTO_SAFETY_STOP 当前属性与本轮记录不一致；' + '很长的诊断内容' * 25
            ui._apply_stats(sample)
            ui.root.update()
            assert [(w.winfo_width(), w.winfo_height()) for w in fixed_widgets] == baseline_sizes, 'dynamic data changed panel sizes'
            lines = ui._vars['last_action'].get().splitlines()
            assert len(lines) <= 2
            assert all(ui._status_font.measure(line) <= ui._status_label.winfo_width()-4 for line in lines), 'status text clipped'
        # Successful completion is a once-per-run notification, not a text
        # match that could accidentally treat a safety stop as completion.
        from unittest.mock import patch
        with patch('imprint_decompose.ui_v2.messagebox.showinfo') as popup:
            sample.auto_completion_id = 1
            sample.auto_phase = '全部完成'
            sample.program_status = '自动处理完成'
            ui._apply_stats(sample)
            ui._apply_stats(sample)
            popup.assert_called_once()
        pending = list(ui.root.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            assert 'scrollbar' not in widget.winfo_class().lower(), 'main page must not scroll'
            if widget.winfo_ismapped():
                assert widget.winfo_rooty() >= ui.root.winfo_rooty(), 'widget above window'
                assert widget.winfo_rootx() >= ui.root.winfo_rootx(), 'widget left of window'
                assert widget.winfo_rooty() + widget.winfo_height() <= ui.root.winfo_rooty() + ui.root.winfo_height() + 1, 'widget below window'
                assert widget.winfo_rootx() + widget.winfo_width() <= ui.root.winfo_rootx() + ui.root.winfo_width() + 1, 'widget right of window'
                parent = widget.master
                assert widget.winfo_y() >= 0 and widget.winfo_x() >= 0, 'child outside parent'
                assert widget.winfo_y() + widget.winfo_height() <= parent.winfo_height() + 1, f'child clipped vertically: {widget}'
                assert widget.winfo_x() + widget.winfo_width() <= parent.winfo_width() + 1, f'child clipped horizontally: {widget}'
        assert ui.root.winfo_height() <= ui.root.winfo_screenheight() - 60, 'window exceeds screen'
        result.update(version=__version__, ui_size=[ui.root.winfo_width(), ui.root.winfo_height()],
                      model_inference='passed', threshold_settings='passed',
                      automatic_mode_controls='passed',
                      startup_defaults='passed',
                      live_mode_refresh='passed',
                      fixed_status_layout='passed',
                      color_keep_switch='passed',
                      v4_filter_and_footer_layout='passed',
                      completion_notification='passed',
                      hidden_startup='passed',
                      single_page_layout='passed', success=True)
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if controller:
            controller.on_stats = None
            controller.shutdown()
        if ui:
            ui.root.destroy()
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if result['success'] else 1
