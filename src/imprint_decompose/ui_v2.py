"""Version 3.1 fixed Apple-inspired presentation."""
import copy
from datetime import datetime
import queue
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from . import APP_NAME, __author__, __version__
from .error_logging import save_error_report
from .ui import ImprintDecomposeUI as BaseUI, RoundedButton
from .controller import ELEMENT_ORDER


class ConfirmationSwitch(tk.Canvas):
    """Compact two-state segmented control."""

    def __init__(self, parent, variable, command):
        super().__init__(parent, width=158, height=36, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, cursor='hand2')
        self.variable = variable
        self.command = command
        self.bind('<Button-1>', self._toggle)
        self.variable.trace_add('write', lambda *_: self._draw())
        self._draw()

    def _toggle(self, _event):
        self.variable.set('manual' if self.variable.get() == 'auto' else 'auto')
        self.command()

    def _draw(self):
        self.delete('all')
        automatic = self.variable.get() == 'auto'
        color = '#007aff' if automatic else '#34c759'
        self._round_rect(1, 1, 157, 35, 9, fill='#f2f2f7', outline='#d8d8dc')
        x0, x1 = ((3, 79) if automatic else (79, 155))
        self._round_rect(x0, 3, x1, 33, 7, fill=color, outline=color)
        self.create_text(41, 18, text='自动', fill='white' if automatic else '#6e6e73',
                         font=('Microsoft YaHei UI', 10, 'bold' if automatic else 'normal'))
        self.create_text(117, 18, text='手动', fill='white' if not automatic else '#6e6e73',
                         font=('Microsoft YaHei UI', 10, 'bold' if not automatic else 'normal'))

    def _round_rect(self, x0, y0, x1, y1, radius, **kwargs):
        points = (x0 + radius, y0, x1 - radius, y0, x1, y0,
                  x1, y0 + radius, x1, y1 - radius, x1, y1,
                  x1 - radius, y1, x0 + radius, y1, x0, y1,
                  x0, y1 - radius, x0, y0 + radius, x0, y0)
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class EnhancementSwitch(tk.Canvas):
    """High-contrast on/off control for automatic enhancement."""

    def __init__(self, parent, variable, command):
        super().__init__(parent, width=150, height=44, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, cursor='hand2')
        self.variable = variable
        self.command = command
        self.bind('<Button-1>', self._toggle)
        self.variable.trace_add('write', lambda *_: self._draw())
        self._draw()

    def _toggle(self, _event):
        self.variable.set(not self.variable.get())
        self.command()

    def _draw(self):
        self.delete('all')
        enabled = bool(self.variable.get())
        active = '#007aff' if enabled else '#8e8e93'
        ConfirmationSwitch._round_rect(
            self, 1, 1, 149, 43, 11, fill='#f2f2f7', outline='#d8d8dc'
        )
        x0, x1 = ((3, 75) if enabled else (75, 147))
        ConfirmationSwitch._round_rect(
            self, x0, 3, x1, 41, 9, fill=active, outline=active
        )
        self.create_text(39, 22, text='开', fill='white' if enabled else '#6e6e73',
                         font=('Microsoft YaHei UI', 12, 'bold' if enabled else 'normal'))
        self.create_text(111, 22, text='关', fill='white' if not enabled else '#6e6e73',
                         font=('Microsoft YaHei UI', 12, 'bold' if not enabled else 'normal'))


class ImprintDecomposeUI(BaseUI):
    BG = '#f5f5f7'
    CARD = '#ffffff'
    FIELD = '#f2f2f7'
    TEXT = '#1d1d1f'
    MUTED = '#6e6e73'

    def __init__(self, controller):
        self._updates = queue.SimpleQueue()
        self._element_images = {}
        self._last_error_logged = None
        self._last_kept_count = 0
        self._keep_alert_active = False
        self._keep_combination = ''
        self._threshold_initialized = False
        super().__init__(controller)
        self.root.title(APP_NAME)
        self._set_window_icon()
        self.root.report_callback_exception = self._on_callback_error
        self.root.attributes('-alpha', 1.0)
        # Keep a fixed desktop layout, but reserve enough vertical space for
        # the complete statistics card and footer at normal Windows DPI.
        self.root.geometry('760x940')
        self.root.resizable(False, False)
        self._apply_stats(controller.stats)
        self.root.after(80, self._drain)

    def _on_stats_threadsafe(self, stats):
        self._updates.put(copy.deepcopy(stats))

    def _drain(self):
        latest = None
        while not self._updates.empty():
            latest = self._updates.get()
        if latest is not None:
            self._apply_stats(latest)
        self.root.after(80, self._drain)

    def _apply_stats(self, stats):
        usage_status, usage_ok = self._window_usage_status(stats.window_size)
        mapping = {
            'program_status': stats.program_status,
            'window_status': stats.window_status,
            'current_resolution': usage_status,
            'last_action': self._friendly_action(stats.last_action),
            'total_decomposed': str(stats.total_decomposed),
            'total_kept': str(stats.total_kept),
            'enhancement_clicks': str(stats.enhancement_clicks),
        }
        for key, value in mapping.items():
            self._vars[key].set(value or '—')
        self._usage_status_label.configure(
            fg='#248a3d' if usage_ok else '#ff3b30'
        )
        for element in ELEMENT_ORDER:
            self._element_vars[element].set(str(stats.element_counts.get(element, 0)))
        self._enhancement_enabled_var.set(stats.enhancement_enabled)
        self._enhancement_rounds_var.set(str(stats.enhancement_target))
        if not self._threshold_initialized:
            self._enhancement_threshold_var.set(
                f'{float(stats.red_attribute_threshold):g}'
            )
            self._threshold_initialized = True
        self._update_threshold_note()
        self._confirmation_mode_var.set('manual' if stats.confirmation_mode == '手动确认' else 'auto')

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

        self._combination_text.configure(state='normal')
        self._combination_text.delete('1.0', 'end')
        lines = [f'{label}  ×  {count}' for label, count in stats.combination_counts.items()]
        self._combination_text.insert('1.0', '\n'.join(lines) or '—')
        self._combination_text.configure(state='disabled')

        error = (stats.last_error or '').strip()
        if error and error != '-' and error != self._last_error_logged:
            self._last_error_logged = error
            self._save_error_snapshot(error, stats)

    def _window_usage_status(self, size_text):
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
    def _friendly_action(value):
        text = (value or '').strip()
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
        operator = '=' if value in ('27', '27.0') else '≥'
        self._threshold_note_var.set(
            f'保留：红色词条 {operator} {value}% 或未出现第三种颜色；数值无法确认时暂停'
        )

    def _on_enhancement_settings_changed(self, _event=None, *, show_error=True):
        result = super()._on_enhancement_settings_changed(
            _event, show_error=show_error
        )
        self._update_threshold_note()
        return result

    def _element_icon(self, element):
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
        icon = icon.resize((34, 34), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(icon, master=self.root)

    def _panel(self, parent, title):
        frame = tk.Frame(parent, bg=self.CARD, padx=16, pady=8,
                         highlightbackground='#dedee3', highlightthickness=1)
        frame.pack(fill='x', pady=(0, 8))
        if title:
            self._label(frame, title, size=12, bold=True).pack(anchor='w', pady=(0, 5))
        return frame

    def _segment(self, parent, variable, choices, command):
        rail = tk.Frame(parent, bg='#f2f2f7', padx=2, pady=2)
        items = []
        for text, value in choices:
            def select(choice=value):
                variable.set(choice)
                command()

            button = tk.Button(
                rail, text=text, command=select, relief='flat', bd=0,
                padx=10, pady=3, font=('Microsoft YaHei UI', 9), cursor='hand2',
            )
            button.pack(side='left', fill='x', expand=True, padx=1)
            items.append((button, value))

        def refresh(*_args):
            selected = variable.get()
            for button, value in items:
                active = selected == value
                button.configure(
                    bg='#007aff' if active else '#f2f2f7',
                    fg='white' if active else self.MUTED,
                    activebackground='#0a84ff' if active else '#e5e5ea',
                    activeforeground='white' if active else '#006bea',
                )

        variable.trace_add('write', refresh)
        refresh()
        return rail

    def _label(self, parent, text, *, size=10, **kwargs):
        widget = super()._label(parent, text, size=size, **kwargs)
        widget.configure(font=('Microsoft YaHei UI', size, 'bold' if kwargs.get('bold') else 'normal'))
        return widget

    def _build(self):
        outer = tk.Frame(self.root, bg=self.BG)
        outer.pack(fill='both', expand=True, padx=18, pady=(10, 8))

        # Pack the footer first so it always reserves space at the bottom.
        footer = tk.Frame(outer, bg=self.BG)
        footer.pack(side='bottom', fill='x', pady=(3, 0))
        coffee = self._label(
            footer, '☕ 如果你觉得这个工具不错，可以请我喝一杯咖啡',
            fg='#007aff', bg=self.BG, size=9, cursor='hand2',
        )
        coffee.configure(font=('Microsoft YaHei UI', 9, 'underline'))
        coffee.pack(anchor='center', pady=(0, 3))
        coffee.bind('<Button-1>', self._show_coffee_qr)
        footer_meta = tk.Frame(footer, bg=self.BG)
        footer_meta.pack(fill='x')
        self._label(footer_meta, '仅供开发交流 · 非盈利 · 开源非商业使用', fg=self.MUTED,
                    bg=self.BG, size=9).pack(side='left')
        self._label(footer_meta, f'作者：{__author__}   ·   v{__version__}', fg=self.MUTED,
                    bg=self.BG, size=9).pack(side='right')

        head = tk.Frame(outer, bg=self.BG)
        head.pack(fill='x', pady=(0, 7))
        hour = datetime.now().hour
        period = '早上' if 5 <= hour < 12 else ('下午' if 12 <= hour < 18 else '晚上')
        self._label(
            head, f'{period}好，祝你出极品刻印', size=15, bold=True, bg=self.BG,
        ).pack(side='left')
        status = tk.Frame(head, bg=self.BG)
        status.pack(side='right')
        self._vars['program_status'] = tk.StringVar(value='已停止')
        self._label(status, '', textvariable=self._vars['program_status'], fg='#007aff',
                    bg=self.BG, bold=True, anchor='e').pack(anchor='e')
        self._vars['window_status'] = tk.StringVar(value='未找到窗口')
        self._label(status, '', textvariable=self._vars['window_status'], fg=self.MUTED,
                    bg=self.BG, size=9, anchor='e').pack(anchor='e')
        reference = self.controller.cfg['window']
        suggestion = f"建议分辨率：{reference['reference_width']}×{reference['reference_height']}"
        self._label(status, suggestion, fg=self.MUTED,
                    bg=self.BG, size=9, anchor='e').pack(anchor='e')
        self._vars['current_resolution'] = tk.StringVar(value='当前无法使用')
        self._usage_status_label = self._label(
            status, '', textvariable=self._vars['current_resolution'], fg='#ff3b30',
            bg=self.BG, size=9, bold=True, anchor='e',
        )
        self._usage_status_label.pack(anchor='e')
        toolbar = tk.Frame(outer, bg=self.BG)
        toolbar.pack(fill='x', pady=(0, 8))
        for text, command, color, hover, fg in (
            ('开始  F9', self.controller.start, '#0071e3', '#0077ed', 'white'),
            ('暂停', self.controller.pause, '#ffffff', '#e5e5ea', self.TEXT),
            ('停止  F10', self.controller.stop, '#ff3b30', '#ff453a', 'white')):
            RoundedButton(toolbar, text=text, command=command, width=132,
                          bg=color, hover_bg=hover, fg=fg).pack(side='left', padx=(0, 10))

        reminder = tk.Frame(
            outer, bg='#fff8e6', padx=14, pady=7,
            highlightbackground='#ffd27a', highlightthickness=1,
        )
        reminder.pack(fill='x', pady=(0, 8))
        self._label(
            reminder, '使用前请手动勾选“本次登录不再提醒”',
            fg='#a65f00', bg='#fff8e6', size=10, bold=True,
        ).pack(anchor='center')

        settings = self._panel(outer, '')
        line = tk.Frame(settings, bg=self.CARD)
        line.pack(fill='x')
        self._label(line, '分解确认', bold=True).pack(side='left')
        ConfirmationSwitch(line, self._confirmation_mode_var,
                           self._on_confirmation_mode_changed).pack(side='right')
        tk.Frame(settings, bg='#e5e5ea', height=1).pack(fill='x', pady=8)
        line = tk.Frame(settings, bg=self.CARD)
        line.pack(fill='x')
        self._label(line, '自动强化', bold=True).pack(side='left')
        EnhancementSwitch(line, self._enhancement_enabled_var,
                          self._on_enhancement_settings_changed).pack(side='right')
        options = tk.Frame(settings, bg=self.CARD)
        options.pack(fill='x', pady=(7, 0))
        self._segment(options, self._enhancement_rounds_var,
                      [('1 次', '1'), ('2 次', '2'), ('3 次', '3')],
                      self._on_enhancement_settings_changed).pack(side='left')
        threshold_box = tk.Frame(options, bg=self.CARD)
        threshold_box.pack(side='right')
        self._label(threshold_box, '红色词条', fg=self.MUTED).pack(side='left', padx=(0, 7))
        self._segment(
            threshold_box, self._enhancement_threshold_var,
            [('≥10%', '10'), ('≥15%', '15'), ('≥20%', '20'), ('=27%', '27')],
            self._on_enhancement_settings_changed,
        ).pack(side='left')
        self._threshold_note_var = tk.StringVar()
        self._enhancement_threshold_var.trace_add('write', self._update_threshold_note)
        self._update_threshold_note()
        self._label(settings, '', textvariable=self._threshold_note_var,
                    fg=self.MUTED, size=9).pack(anchor='e', pady=(5, 0))

        metrics = self._panel(outer, '')
        for i, (title, key) in enumerate((('已分解', 'total_decomposed'), ('已保留', 'total_kept'), ('强化次数', 'enhancement_clicks'))):
            cell = tk.Frame(metrics, bg=self.CARD)
            cell.grid(row=0, column=i, sticky='ew')
            metrics.columnconfigure(i, weight=1, uniform='metric')
            var = tk.StringVar(value='0')
            self._vars[key] = var
            self._label(cell, '', textvariable=var, size=20, bold=True, fg='#007aff').pack(anchor='w')
            self._label(cell, title, fg=self.MUTED).pack(anchor='w')

        current = self._panel(outer, '')
        current_top = tk.Frame(current, bg=self.CARD)
        current_top.pack(fill='x')
        self._label(current_top, '当前刻印', fg=self.MUTED).pack(side='left')
        alert_slot = tk.Frame(current_top, bg=self.CARD, width=180, height=38)
        alert_slot.pack(side='right')
        alert_slot.pack_propagate(False)
        self._keep_alert = tk.Button(alert_slot, text='需要注意 · 已保留', command=self._dismiss_keep_alert,
                                     bg='#e53643', fg='white', activebackground='#c92431',
                                     activeforeground='white', relief='flat', bd=0, padx=14, pady=6,
                                     cursor='hand2', font=('Microsoft YaHei UI', 10, 'bold'))
        self._displayed_combination = None
        self._current_icons = tk.Frame(current, bg=self.CARD)
        self._current_icons.pack(fill='x', pady=(5, 0), ipady=1)
        self._vars['last_action'] = tk.StringVar(value='等待选卡')
        action = self._label(current, '', textvariable=self._vars['last_action'], fg=self.MUTED, anchor='w', justify='left')
        action.pack(fill='x', pady=(4, 0))
        action.bind('<Configure>', lambda e: action.configure(wraplength=max(200, e.width-8)))

        stats = self._panel(outer, '本次刻印分解统计')
        strip = tk.Frame(stats, bg=self.CARD)
        strip.pack(fill='x', pady=(0, 6))
        for element in ELEMENT_ORDER:
            var = tk.StringVar(value='0')
            self._element_vars[element] = var
            cell = tk.Frame(strip, bg=self.CARD)
            cell.pack(side='left', expand=True)
            try:
                icon = self._element_icon(element)
                self._element_images[element] = icon
                tk.Label(cell, image=icon, bg=self.CARD).pack(side='left')
            except (FileNotFoundError, OSError, KeyError):
                self._label(cell, element, fg=self.MUTED).pack(side='left')
            self._label(cell, '', textvariable=var, size=14, bold=True).pack(side='left', padx=(4, 0))
        self._label(stats, '组合', fg=self.MUTED).pack(anchor='w')
        self._combination_text = self._text(stats)

    def _text(self, parent):
        scroll = ttk.Scrollbar(parent)
        scroll.pack(side='right', fill='y')
        widget = tk.Text(parent, height=2, bg=self.CARD, fg=self.TEXT, relief='flat',
                         font=('Microsoft YaHei UI', 10), wrap='word', state='disabled',
                         highlightthickness=0, padx=4, pady=4, yscrollcommand=scroll.set)
        widget.pack(fill='both', expand=True)
        scroll.configure(command=widget.yview)
        return widget
