"""Single-page control panel with all primary information visible."""
import copy
from datetime import datetime
import queue
import re
import sys
import tkinter as tk
from tkinter import messagebox
from tkinter.font import Font
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from . import APP_NAME, __author__, __version__
from .error_logging import save_error_report
from .ui import ImprintDecomposeUI as BaseUI, RoundedButton
from .controller import ELEMENT_ORDER


class SegmentedControl(tk.Canvas):
    """Compact two-state segmented control."""

    def __init__(self, parent, variable, command, choices, *, width=None, height=36):
        self.choices = tuple(choices)
        font = Font(root=parent, family='Microsoft YaHei UI', size=9, weight='bold')
        width = width or (max(font.measure(label) for label, _ in choices)+22)*len(choices)+6
        self._control_width, self._control_height = width, height
        super().__init__(parent, width=width, height=height, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, cursor='hand2')
        self.variable = variable
        self.command = command
        self.bind('<Button-1>', self._toggle)
        self.variable.trace_add('write', lambda *_: self._draw())
        self._draw()

    def _toggle(self, event):
        index = max(0, min(len(self.choices)-1, int(event.x*len(self.choices)/self._control_width)))
        value = self.choices[index][1]
        if self.variable.get() != value:
            self.variable.set(value)
            self.command()

    def _draw(self):
        self.delete('all')
        w, h = self._control_width, self._control_height
        step = (w-6)/len(self.choices)
        self._round_rect(1, 1, w-1, h-1, 9, fill='#f2f2f7', outline='#d8d8dc')
        for index, (label, value) in enumerate(self.choices):
            active = self.variable.get() == value
            left = 3+index*step
            if active:
                self._round_rect(left, 3, left+step, h-3, 7, fill='#007aff', outline='#007aff')
            self.create_text(left+step/2, h/2, text=label,
                             fill='white' if active else '#6e6e73',
                             font=('Microsoft YaHei UI', 9, 'bold' if active else 'normal'))

    def _round_rect(self, x0, y0, x1, y1, radius, **kwargs):
        points = (x0 + radius, y0, x1 - radius, y0, x1, y0,
                  x1, y0 + radius, x1, y1 - radius, x1, y1,
                  x1 - radius, y1, x0 + radius, y1, x0, y1,
                  x0, y1 - radius, x0, y0 + radius, x0, y0)
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class ConfirmationSwitch(SegmentedControl):
    def __init__(self, parent, variable, command):
        super().__init__(parent, variable, command, [('自动', 'auto'), ('手动', 'manual')], width=158)


class EnhancementSwitch(SegmentedControl):
    """On/off options share the same appearance as all other choices."""

    def __init__(self, parent, variable, command, *, width=150, height=36):
        super().__init__(parent, variable, command, [('开', True), ('关', False)],
                         width=width, height=height)


class ImprintDecomposeUI(BaseUI):
    BG = '#f5f5f7'
    CARD = '#ffffff'
    FIELD = '#f2f2f7'
    TEXT = '#1d1d1f'
    MUTED = '#6e6e73'

    def __init__(self, controller):
        self._updates = queue.Queue(maxsize=1)
        self._element_images = {}
        self._count_images = {}
        self._last_error_logged = None
        self._last_kept_count = 0
        self._keep_alert_active = False
        self._keep_combination = ''
        self._threshold_initialized = False
        self._last_completion_id = 0
        super().__init__(controller)
        self.root.title(f'{APP_NAME} v{__version__}')
        self._set_window_icon()
        self.root.report_callback_exception = self._on_callback_error
        self.root.resizable(True, True)
        self._apply_stats(controller.stats)
        self._fit_content(initial=True)
        self._startup_hidden = not self.root.winfo_ismapped()
        self.root.deiconify()
        self.root.after(80, self._drain)

    def _fit_content(self, *, initial=False):
        self.root.update_idletasks()
        if initial:
            # Measure once for the actual Windows font/DPI. Later status or
            # counter changes cannot change these panels or the window size.
            self._bottom.configure(height=max(self._current_panel.winfo_reqheight(),
                                               self._statistics_panel.winfo_reqheight()))
            self.root.update_idletasks()
        width = max(780, self._outer.winfo_reqwidth())
        height = self._outer.winfo_reqheight()
        self.root.minsize(width, height)
        if initial:
            self.root.geometry(f'{width}x{height}')
        elif self.root.winfo_height() < height:
            self.root.geometry(f'{max(width, self.root.winfo_width())}x{height}')

    def _on_stats_threadsafe(self, stats):
        snapshot = copy.deepcopy(stats)
        try:
            self._updates.put_nowait(snapshot)
        except queue.Full:
            try:
                self._updates.get_nowait()
            except queue.Empty:
                pass
            try:
                self._updates.put_nowait(snapshot)
            except queue.Full:
                pass

    def _drain(self):
        latest = None
        while not self._updates.empty():
            latest = self._updates.get()
        if latest is not None:
            self._apply_stats(latest)
        self.root.after(80, self._drain)

    def _apply_stats(self, stats):
        usage_status, usage_ok = self._window_usage_status(
            stats.window_size,
            stats.window_status,
            stats.vision_state,
        )
        mapping = {
            'program_status': stats.program_status,
            'window_status': stats.window_status,
            'current_resolution': self._format_current_resolution(stats.window_size),
            'usage_status': usage_status,
            'last_action': self._wrap_status(self._status_text(stats)),
            'total_decomposed': str(stats.total_decomposed),
            'total_kept': str(stats.total_kept),
            'red_threshold_matches': str(getattr(stats, 'red_threshold_matches', 0)),
            'auto_progress': (
                f'{stats.auto_phase}  ·  已处理 {stats.auto_processed}  ·  本页候选 {stats.auto_candidates}'
                f'  ·  滚动 {stats.auto_scrolled}  ·  第 {stats.auto_pass} 遍'
                if stats.auto_mode else '手动选择：点开刻印后处理；原本已有 5 属性仍受保护'
            ),
        }
        for key, value in mapping.items():
            self._vars[key].set(value or '—')
        self._usage_status_label.configure(
            fg='#248a3d' if usage_ok else '#ff3b30'
        )
        for element in ELEMENT_ORDER:
            self._element_vars[element].set(str(stats.element_counts.get(element, 0)))
        # Unchanged settings must not repaint controls on every vision frame.
        for variable, value in (
                (self._enhancement_enabled_var, stats.enhancement_enabled),
                (self._selection_mode_var, 'auto' if stats.auto_mode else 'manual'),
                (self._keep_two_elements_var, stats.keep_two_elements),
                (self._enhancement_rounds_var, str(stats.enhancement_target)),
                (self._confirmation_mode_var, 'manual' if stats.confirmation_mode == '手动确认' else 'auto')):
            if variable.get() != value:
                variable.set(value)
        if not self._threshold_initialized:
            self._enhancement_threshold_var.set(
                f'{float(stats.red_attribute_threshold):g}'
            )
            self._threshold_initialized = True
        self._update_threshold_note()

        combination = stats.current_combination or ''
        if combination != self._displayed_combination:
            self._displayed_combination = combination
            self._show_combination(combination)

        kept = stats.total_kept
        if kept > self._last_kept_count:
            self._keep_alert_active = True
            self._keep_combination = combination
        elif (self._keep_alert_active and combination and combination != '-'
              and self._keep_combination and combination != self._keep_combination
              and '保留' not in (stats.last_action or '')):
            self._keep_alert_active = False
        self._last_kept_count = kept
        if self._keep_alert_active:
            self._keep_alert.place(relx=1.0, rely=0.5, anchor='e')
        else:
            self._keep_alert.place_forget()

        self._combination_var.set(self._format_combination_summary(stats.combination_counts))

        error = (stats.last_error or '').strip()
        if error and error != '-' and error != self._last_error_logged:
            self._last_error_logged = error
            self._save_error_snapshot(error, stats)
        self._notify_completion(stats)

    def _notify_completion(self, stats):
        event = stats.auto_completion_id
        if event <= self._last_completion_id:
            return
        self._last_completion_id = event  # mark BEFORE opening a modal dialog
        if stats.auto_phase == '全部完成' and stats.program_status == '自动处理完成':
            messagebox.showinfo(
                '处理完成',
                '完成了\n\n'
                f'本次处理：{stats.auto_processed} 枚\n'
                f'本次保留：{stats.auto_run_kept} 枚\n'
                f'刻印数：{stats.inventory_start} → {stats.inventory_end}',
                parent=self.root,
            )

    @classmethod
    def _status_text(cls, stats):
        text = cls._friendly_action(stats.last_action)
        if stats.auto_phase == '保护暂停' and stats.last_error not in ('', '-', None):
            text = '安全暂停 · ' + str(stats.last_error)
        # Keep the summary two lines high. Full evidence is retained in logs;
        # do not let arbitrarily long diagnostics change the window geometry.
        text = ' '.join(text.split()).replace('选卡', '选择').replace('逐卡', '逐枚')
        return text if len(text) <= 84 else text[:81] + '…'

    def _wrap_status(self, text):
        width = self._status_label.winfo_width()
        limit = min(690, width-8) if width > 20 else 690
        lines, line = [], ''
        for char in text:
            if line and self._status_font.measure(line + char) > limit:
                lines.append(line)
                line = ''
                if len(lines) == 2:
                    while self._status_font.measure(lines[-1] + '…') > limit:
                        lines[-1] = lines[-1][:-1]
                    return '\n'.join(lines[:-1] + [lines[-1] + '…'])
            line += char
        return '\n'.join(lines + [line])

    def _window_usage_status(self, size_text, window_status, vision_state):
        window_match = re.search(
            r'已找到\s*hwnd\s*=\s*(\d+)',
            str(window_status or ''),
            flags=re.IGNORECASE,
        )
        window_found = bool(window_match and int(window_match.group(1)) > 0)
        # Availability here answers whether the detected game window is ready
        # by geometry. The actual imprint-page visual state is checked by the
        # controller before any action, so being on the game home screen must
        # not keep a valid game window marked as unusable.
        if not window_found:
            return '当前无法使用', False

        match = re.search(r'(\d+)\s*[×xX]\s*(\d+)', str(size_text or ''))
        if not match:
            return '当前无法使用', False
        width, height = map(int, match.groups())
        cfg = self.controller.cfg['window']
        expected = float(cfg['reference_width']) / float(cfg['reference_height'])
        ratio_error = abs(width / height - expected) / expected
        usable = (
            width >= int(cfg['minimum_width'])
            and height >= int(cfg['minimum_height'])
            and ratio_error <= float(cfg.get('aspect_ratio_tolerance', 0.05))
        )
        return ('当前可以使用' if usable else '当前无法使用'), usable

    @staticmethod
    def _format_current_resolution(size_text):
        match = re.search(r'(\d+)\s*[×xX]\s*(\d+)', str(size_text or ''))
        if not match:
            return '当前分辨率：—'
        width, height = match.groups()
        return f'当前分辨率：{width}×{height}'

    @staticmethod
    def _format_combination_summary(combination_counts):
        counts = {
            label: int(count)
            for label, count in (combination_counts or {}).items()
            if int(count) > 0
        }
        if not counts:
            return '—'
        return f'组合种类：{len(counts)}    合计：{sum(counts.values())}'

    @staticmethod
    def _friendly_action(value):
        text = (value or '').strip()
        if text.startswith('AUTO_SAFETY_STOP'):
            return '安全暂停 · ' + text[len('AUTO_SAFETY_STOP'):].strip()
        if not text or text == '-':
            return '等待操作'
        technical = (
            'VISION ', 'DETAIL_DIAGNOSTIC', 'CONFIRM_DIAGNOSTIC',
            'REWARD_DIAGNOSTIC', 'LIST_DIAGNOSTIC',
        )
        if text.startswith(technical) or (' phase=' in text and ' state=' in text):
            if 'REWARD' in text:
                return '正在完成分解流程…'
            if 'CONFIRM' in text:
                return '正在确认分解…'
            if 'LIST' in text:
                return '等待选择下一个刻印'
            return '正在识别当前刻印…'
        return text

    def _show_combination(self, combination):
        for child in self._current_icons.winfo_children():
            child.destroy()
        elements = [part.strip() for part in re.split(r'\s*\+\s*', combination)
                    if part.strip() in self._element_images]
        if not elements:
            self._label(self._current_icons, '—', size=13, fg=self.MUTED).pack(side='left')
            return
        for element in elements:
            tk.Label(self._current_icons, image=self._element_images[element],
                     bg=self.CARD).pack(side='left', padx=(0, 4))

    def _dismiss_keep_alert(self):
        self._keep_alert_active = False
        self._keep_alert.place_forget()

    def _save_error_snapshot(self, error, stats):
        save_error_report(
            error,
            context=(f'程序状态: {stats.program_status}\n窗口状态: {stats.window_status}\n'
                     f'识别状态: {stats.vision_state}\n最近动作: {stats.last_action}\n'
                     f'当前组合: {stats.current_combination}'),
        )

    def _on_callback_error(self, error_type, error, trace):
        save_error_report(error, context='Tk 界面回调异常',
                          exc_info=(error_type, error, trace))
        self._vars['last_action'].set('界面出错，过程日志已保存到 logs')

    def _set_window_icon(self):
        base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        icon = base / 'assets' / 'app_icon.ico'
        if icon.exists():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _show_coffee_qr(self, _event=None):
        base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        qr_path = base / 'assets' / 'wechat_pay.jpg'
        if not qr_path.exists():
            self._vars['last_action'].set('未找到收款码图片')
            return

        popup = tk.Toplevel(self.root)
        popup.title('请我喝一杯咖啡')
        popup.configure(bg=self.BG)
        popup.resizable(False, False)
        popup.transient(self.root)
        self._label(
            popup, '☕ 谢谢你的支持', size=14, bold=True, bg=self.BG,
        ).pack(pady=(14, 8))
        with Image.open(qr_path) as source:
            image = source.convert('RGB')
            image.thumbnail((390, 470), Image.Resampling.LANCZOS)
        popup._coffee_image = ImageTk.PhotoImage(image, master=popup)
        tk.Label(popup, image=popup._coffee_image, bg=self.BG).pack(padx=16)
        self._label(
            popup, '微信扫码即可支持作者', fg=self.MUTED, bg=self.BG, size=9,
        ).pack(pady=(8, 14))
        popup.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - popup.winfo_width()) // 2
        y = self.root.winfo_rooty() + max(20, (self.root.winfo_height() - popup.winfo_height()) // 2)
        popup.geometry(f'+{x}+{y}')

    def _update_threshold_note(self, *_args):
        if not hasattr(self, '_threshold_note_var'):
            return
        value = self._enhancement_threshold_var.get().strip() or '20'
        suffix = '无红字时按元素保留' if self._keep_two_elements_var.get() else '元素保留已关闭'
        self._threshold_note_var.set(
            f'红字 ≥{value}% 保留 · {suffix}'
        )

    def _on_selection_mode_changed(self):
        self.controller.set_auto_mode(self._selection_mode_var.get() == 'auto')
        # The setter publishes a normalized snapshot to the queue. Never read
        # the worker's partially updated shared stats directly here.

    def _on_keep_two_elements_changed(self):
        self.controller.set_keep_two_elements(self._keep_two_elements_var.get())
        self._update_threshold_note()

    def _on_enhancement_settings_changed(self, _event=None, *, show_error=True):
        result = super()._on_enhancement_settings_changed(
            _event, show_error=show_error
        )
        self._enhancement_enabled_var.set(self.controller.stats.enhancement_enabled)
        self._enhancement_rounds_var.set(str(self.controller.stats.enhancement_target))
        self._update_threshold_note()
        return result

    def _element_icon(self, element, size=34):
        # Keep the game's original pixels; only remove the surrounding screenshot background.
        atlas = {
            '风暴': ('storm_earth.png', (37, 19, 67, 50)),
            '烈焰': ('flame.png', (14, 15, 45, 46)),
            '电弧': ('arc_shadow.png', (47, 31, 78, 62)),
            '暗影': ('arc_shadow.png', (15, 31, 46, 62)),
            '大地': ('storm_earth.png', (5, 19, 35, 50)),
        }
        filename, box = atlas[element]
        base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
        path = base / 'imprint_decompose' / 'element_icons' / filename
        with Image.open(path) as source:
            icon = source.crop(box).convert('RGBA')
        for corner in ((0, 0), (icon.width - 1, 0),
                       (0, icon.height - 1), (icon.width - 1, icon.height - 1)):
            if icon.getpixel(corner)[3]:
                ImageDraw.floodfill(icon, corner, (0, 0, 0, 0), thresh=45)
        icon = icon.resize((size, size), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(icon, master=self.root)

    def _panel(self, parent, title):
        frame = tk.Frame(parent, bg=self.CARD, padx=16, pady=6,
                         highlightbackground='#dedee3', highlightthickness=1)
        frame.pack(fill='x', pady=(0, 6))
        if title:
            self._label(frame, title, size=12, bold=True).pack(anchor='w', pady=(0, 5))
        return frame

    def _segment(self, parent, variable, choices, command):
        return SegmentedControl(parent, variable, command, choices)

    def _label(self, parent, text, *, size=10, **kwargs):
        widget = super()._label(parent, text, size=size, **kwargs)
        widget.configure(font=('Microsoft YaHei UI', size, 'bold' if kwargs.get('bold') else 'normal'))
        return widget

    def _build(self):
        outer = self._outer = tk.Frame(self.root, bg=self.BG, padx=16, pady=8)
        outer.pack(fill='both', expand=True)

        # Pack the footer first so it always reserves space at the bottom.
        footer = self._footer = tk.Frame(outer, bg=self.BG)
        footer.pack(side='bottom', fill='x', pady=(3, 0))
        for column in range(3):
            footer.columnconfigure(column, weight=1, uniform='footer')
        coffee = self._coffee_link = self._label(
            footer, '☕ 如果你觉得这个工具不错，可以请我喝一杯咖啡',
            fg='#007aff', bg=self.BG, size=9, cursor='hand2',
        )
        coffee.configure(font=('Microsoft YaHei UI', 9, 'underline'))
        coffee.grid(row=0, column=0, columnspan=3, pady=(0, 3))
        coffee.bind('<Button-1>', self._show_coffee_qr)
        self._label(footer, '仅供开发交流 · 非商业使用', fg=self.MUTED,
                    bg=self.BG, size=9).grid(row=1, column=0, sticky='w')
        self._label(footer, f'作者：{__author__}   ·   v{__version__}', fg=self.MUTED,
                    bg=self.BG, size=9).grid(row=1, column=2, sticky='e')

        head = tk.Frame(outer, bg=self.BG)
        head.pack(fill='x', pady=(0, 4))
        hour = datetime.now().hour
        period = '早上' if 5 <= hour < 12 else ('下午' if 12 <= hour < 18 else '晚上')
        self._label(
            head, f'{period}好，祝你出极品刻印', size=15, bold=True, bg=self.BG,
        ).pack(side='left')
        self._vars['program_status'] = tk.StringVar(value='已停止')
        self._label(head, '', textvariable=self._vars['program_status'], fg='#007aff',
                    bg=self.BG, bold=True, anchor='e').pack(side='right')
        status = tk.Frame(outer, bg=self.BG)
        status.pack(fill='x', pady=(0, 6))
        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=1)
        self._vars['window_status'] = tk.StringVar(value='未找到窗口')
        self._label(status, '', textvariable=self._vars['window_status'], fg=self.MUTED,
                    bg=self.BG, size=9, anchor='w').grid(row=0, column=0, sticky='w')
        reference = self.controller.cfg['window']
        suggestion = f"建议分辨率：{reference['reference_width']}×{reference['reference_height']}"
        self._label(status, suggestion, fg=self.MUTED,
                    bg=self.BG, size=9, anchor='w').grid(row=1, column=0, sticky='w')
        self._vars['current_resolution'] = tk.StringVar(value='当前分辨率：—')
        self._label(
            status, '', textvariable=self._vars['current_resolution'], fg=self.MUTED,
            bg=self.BG, size=9, anchor='e',
        ).grid(row=1, column=1, sticky='e')
        self._vars['usage_status'] = tk.StringVar(value='当前无法使用')
        self._usage_status_label = self._label(
            status, '', textvariable=self._vars['usage_status'], fg='#ff3b30',
            bg=self.BG, size=9, bold=True, anchor='e',
        )
        self._usage_status_label.grid(row=0, column=1, sticky='e')
        toolbar = tk.Frame(outer, bg=self.BG)
        toolbar.pack(fill='x', pady=(0, 8))
        for text, command, color, hover, fg in (
            ('开始  F9', self.controller.start, '#0071e3', '#0077ed', 'white'),
            ('暂停', self.controller.pause, '#ffffff', '#e5e5ea', self.TEXT),
            ('停止  F10', self.controller.stop, '#ff3b30', '#ff453a', 'white')):
            RoundedButton(toolbar, text=text, command=command, width=132,
                          bg=color, hover_bg=hover, fg=fg).pack(side='left', padx=(0, 10))
        self._label(toolbar, 'Esc 暂停 · F11 调试', bg=self.BG,
                    fg=self.MUTED, size=9).pack(side='right')

        reminder = tk.Frame(
            outer, bg='#fff8e6', padx=14, pady=1,
            highlightbackground='#ffd27a', highlightthickness=1,
        )
        reminder.pack(fill='x', pady=(0, 6))
        self._label(
            reminder, '使用前请手动勾选“本次登录不再提醒”',
            fg='#a65f00', bg='#fff8e6', size=9, bold=True,
        ).pack(anchor='center')

        settings = self._panel(outer, '')
        selection = tk.Frame(settings, bg=self.CARD)
        selection.pack(fill='x', pady=(0, 6))
        self._label(selection, '选择方式', bold=True).pack(side='left', padx=(0, 10))
        self._selection_mode_var = tk.StringVar(value='auto' if self.controller.stats.auto_mode else 'manual')
        self._segment(selection, self._selection_mode_var,
                      [('手动选择', 'manual'), ('自动扫描', 'auto')],
                      self._on_selection_mode_changed).pack(side='left')
        self._label(selection, '自动仅处理初始 2 属性 · 原有 3/4/5 跳过',
                    fg=self.MUTED, size=9).pack(side='right')
        controls = tk.Frame(settings, bg=self.CARD)
        controls.pack(fill='x')
        actions = tk.Frame(controls, bg=self.CARD)
        actions.pack(side='left', anchor='n', padx=(0, 14))
        self._label(actions, '分解确认', bold=True).grid(row=0, column=0, sticky='w', padx=(0, 8))
        ConfirmationSwitch(actions, self._confirmation_mode_var,
                           self._on_confirmation_mode_changed).grid(row=0, column=1, sticky='w')
        self._label(actions, '自动强化', bold=True).grid(row=1, column=0, sticky='w', pady=5)
        EnhancementSwitch(actions, self._enhancement_enabled_var,
                          self._on_enhancement_settings_changed, width=158).grid(row=1, column=1, sticky='w', pady=5)
        self._label(actions, '强化次数', fg=self.MUTED, size=9).grid(row=2, column=0, sticky='w')
        self._segment(actions, self._enhancement_rounds_var,
                      [('1 次', '1'), ('2 次', '2'), ('3 次', '3')],
                      self._on_enhancement_settings_changed).grid(row=2, column=1, sticky='w')
        filters = self._filter_panel = tk.Frame(controls, bg=self.CARD, padx=8, pady=4,
                                                highlightbackground='#dedee3', highlightthickness=1)
        filters.pack(side='right', fill='both', expand=True)
        threshold_box = tk.Frame(filters, bg=self.CARD)
        threshold_box.pack(fill='x')
        self._label(threshold_box, '红色词条', fg=self.MUTED, size=9).pack(side='left', padx=(0, 5))
        self._segment(
            threshold_box, self._enhancement_threshold_var,
            [('≥10%', '10'), ('≥15%', '15'), ('≥20%', '20'), ('≥27%', '27')],
            self._on_enhancement_settings_changed,
        ).pack(side='right')
        color_rule = tk.Frame(filters, bg=self.CARD)
        color_rule.pack(fill='x', pady=(4, 2))
        self._keep_two_elements_var = tk.BooleanVar(value=self.controller.stats.keep_two_elements)
        self._label(color_rule, '保留未出现第三种元素', fg=self.MUTED, size=9).pack(side='left', padx=(0, 7))
        self._color_keep_switch = EnhancementSwitch(
            color_rule, self._keep_two_elements_var, self._on_keep_two_elements_changed, width=110)
        self._color_keep_switch.pack(side='right')
        self._threshold_note_var = tk.StringVar()
        self._enhancement_threshold_var.trace_add('write', self._update_threshold_note)
        self._update_threshold_note()
        self._label(filters, '', textvariable=self._threshold_note_var,
                    fg=self.MUTED, size=9).pack(anchor='w')

        metrics = self._panel(outer, '')
        for i, (title, key) in enumerate((
            ('已分解', 'total_decomposed'),
            ('已保留', 'total_kept'),
            ('红色达标', 'red_threshold_matches'),
        )):
            cell = tk.Frame(metrics, bg=self.CARD)
            cell.grid(row=0, column=i, sticky='ew')
            metrics.columnconfigure(i, weight=1, uniform='metric')
            var = tk.StringVar(value='0')
            self._vars[key] = var
            self._label(cell, title, fg=self.MUTED).pack(side='left', padx=(0, 10))
            self._label(cell, '', textvariable=var, size=19, bold=True, fg='#007aff').pack(side='left')

        bottom = self._bottom = tk.Frame(outer, bg=self.BG, height=114)
        bottom.pack(fill='x', pady=(0, 8))
        bottom.pack_propagate(False)
        bottom.grid_propagate(False)
        bottom.rowconfigure(0, weight=1)
        bottom.columnconfigure(0, weight=2, uniform='bottom')
        bottom.columnconfigure(1, weight=3, uniform='bottom')
        current = self._current_panel = tk.Frame(bottom, bg=self.CARD, padx=12, pady=6,
                           highlightbackground='#dedee3', highlightthickness=1)
        current.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        current_top = tk.Frame(current, bg=self.CARD)
        current_top.pack(fill='x')
        self._label(current_top, '当前刻印', fg=self.MUTED).pack(side='left')
        alert_slot = tk.Frame(current_top, bg=self.CARD, width=130, height=26)
        alert_slot.pack(side='right')
        alert_slot.pack_propagate(False)
        self._keep_alert = tk.Button(alert_slot, text='已保留 · 知道了', command=self._dismiss_keep_alert,
                                     bg='#e8f5ec', fg='#248a3d', activebackground='#d1ebda',
                                     activeforeground='#248a3d', relief='flat', bd=0, padx=6, pady=2,
                                     cursor='hand2', font=('Microsoft YaHei UI', 9))
        self._displayed_combination = None
        self._current_icons = tk.Frame(current, bg=self.CARD, height=38)
        self._current_icons.pack(fill='x', pady=(5, 0), ipady=1)
        self._current_icons.pack_propagate(False)
        self._label(current, '元素按实际槽位顺序显示', fg=self.MUTED, size=9).pack(anchor='w')

        stats = self._statistics_panel = tk.Frame(bottom, bg=self.CARD, padx=12, pady=6,
                         highlightbackground='#dedee3', highlightthickness=1)
        stats.grid(row=0, column=1, sticky='nsew', padx=(4, 0))
        self._label(stats, '本次刻印分解统计', size=10, bold=True).pack(anchor='w')
        strip = tk.Frame(stats, bg=self.CARD)
        strip.pack(fill='x', pady=(6, 6))
        for i, element in enumerate(ELEMENT_ORDER):
            var = tk.StringVar(value='0')
            self._element_vars[element] = var
            cell = tk.Frame(strip, bg=self.CARD)
            cell.grid(row=0, column=i, sticky='ew')
            strip.columnconfigure(i, weight=1, uniform='elements')
            try:
                icon = self._element_icon(element)
                self._element_images[element] = icon
                count_icon = self._element_icon(element, size=26)
                self._count_images[element] = count_icon
                tk.Label(cell, image=count_icon, bg=self.CARD).pack(side='left')
            except (FileNotFoundError, OSError, KeyError):
                self._label(cell, element, fg=self.MUTED).pack(side='left')
            self._label(cell, '', textvariable=var, size=10, bold=True, width=4, anchor='w').pack(side='left', padx=(4, 0))
        self._combination_var = tk.StringVar(value='—')
        self._label(stats, '', textvariable=self._combination_var,
                    fg=self.MUTED, size=9, wraplength=320, justify='left').pack(anchor='w')

        state = self._state_panel = self._panel(outer, '')
        state_head = tk.Frame(state, bg=self.CARD)
        state_head.pack(fill='x')
        self._label(state_head, '运行状态', bold=True, size=10).pack(side='left')
        self._vars['auto_progress'] = tk.StringVar(value='待开始')
        self._label(state_head, '', textvariable=self._vars['auto_progress'],
                    fg='#0071e3', size=9).pack(side='right')
        self._vars['last_action'] = tk.StringVar(value='等待选择')
        action = self._label(state, '', textvariable=self._vars['last_action'], fg=self.MUTED,
                             anchor='w', justify='left', height=2, wraplength=690, size=9)
        self._status_label = action
        self._status_font = Font(root=self.root, font=action.cget('font'))
        action.pack(fill='x', pady=(3, 0))
        self._error_hint = self._label(
            outer, '遇到错误？请提交 EXE 同目录的 logs 和 debug 文件夹；读不清时会暂停保护。',
            bg=self.BG, fg=self.MUTED, size=9)
        self._error_hint.pack(anchor='w', pady=(0, 2))
