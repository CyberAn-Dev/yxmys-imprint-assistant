"""Version 2 presentation; controller and recognition remain unchanged."""
import copy
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .ui import ImprintDecomposeUI as BaseUI, RoundedButton
from .controller import ELEMENT_ORDER


class ImprintDecomposeUI(BaseUI):
    BG = '#edf3fa'
    CARD = '#ffffff'
    FIELD = '#f4f7fb'
    TEXT = '#182c45'
    MUTED = '#6c7e94'

    def __init__(self, controller):
        self._updates = queue.SimpleQueue()
        self._element_images = {}
        super().__init__(controller)
        self.root.title('刻印 · v2.0')
        self.root.attributes('-alpha', 1.0)
        self.root.resizable(True, True)
        self.root.minsize(720, 800)
        height = min(850, self.root.winfo_screenheight() - 100)
        self.root.geometry(f'780x{height}')
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
        super()._apply_stats(stats)
        for element in ELEMENT_ORDER:
            self._element_vars[element].set(str(stats.element_counts.get(element, 0)))

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
        frame = tk.Frame(parent, bg=self.CARD, padx=18, pady=12,
                         highlightbackground='#dde6f0', highlightthickness=1)
        frame.pack(fill='x', pady=(0, 12))
        if title:
            self._label(frame, title, size=12, bold=True).pack(anchor='w', pady=(0, 8))
        return frame

    def _segment(self, parent, variable, choices, command):
        rail = tk.Frame(parent, bg='#eaf0f8', padx=3, pady=3)
        items = []
        for text, value in choices:
            button = tk.Radiobutton(rail, text=text, value=value, variable=variable,
                command=command, indicatoron=False, relief='flat', bd=0,
                padx=16, pady=5, font=('Microsoft YaHei UI', 10),
                bg='#eaf0f8', fg=self.MUTED, selectcolor='white',
                activebackground='#dceaff', activeforeground='#006bea', cursor='hand2')
            button.pack(side='left', fill='x', expand=True, padx=1)
            items.append(button)
        return rail

    def _label(self, parent, text, *, size=10, **kwargs):
        widget = super()._label(parent, text, size=size, **kwargs)
        widget.configure(font=('Microsoft YaHei UI', size, 'bold' if kwargs.get('bold') else 'normal'))
        return widget

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TNotebook', background=self.BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(20, 8), font=('Microsoft YaHei UI', 10),
                        background='#e8eef7', foreground=self.MUTED)
        style.map('TNotebook.Tab', background=[('selected', 'white')], foreground=[('selected', '#007aff')])
        outer = tk.Frame(self.root, bg=self.BG)
        outer.pack(fill='both', expand=True, padx=22, pady=18)
        head = tk.Frame(outer, bg=self.BG)
        head.pack(fill='x', pady=(0, 14))
        self._label(head, '刻印', size=25, bold=True, bg=self.BG).pack(side='left')
        self._label(head, 'v2.0', fg=self.MUTED, bg=self.BG).pack(side='left', padx=12, pady=(12, 0))
        self._vars['program_status'] = tk.StringVar(value='已停止')
        self._label(head, '', textvariable=self._vars['program_status'], fg='#007aff', bg=self.BG).pack(side='right')
        toolbar = tk.Frame(outer, bg=self.BG)
        toolbar.pack(fill='x', pady=(0, 18))
        for text, command, color, hover, fg in (
            ('开始  F9', self.controller.start, '#007aff', '#268eff', 'white'),
            ('暂停', self.controller.pause, '#ffffff', '#e4efff', self.TEXT),
            ('停止  F10', self.controller.stop, '#e6eef9', '#d6e5f8', '#35618d')):
            RoundedButton(toolbar, text=text, command=command, width=132,
                          bg=color, hover_bg=hover, fg=fg).pack(side='left', padx=(0, 10))

        settings = self._panel(outer, '')
        line = tk.Frame(settings, bg=self.CARD)
        line.pack(fill='x')
        self._label(line, '分解确认', bold=True).pack(side='left')
        self._segment(line, self._confirmation_mode_var,
                      [('自动', 'auto'), ('手动', 'manual')], self._on_confirmation_mode_changed).pack(side='right')
        tk.Frame(settings, bg='#edf1f6', height=1).pack(fill='x', pady=12)
        line = tk.Frame(settings, bg=self.CARD)
        line.pack(fill='x')
        self._label(line, '强化', bold=True).pack(side='left')
        self._check(line, '启用', self._enhancement_enabled_var,
                    self._on_enhancement_settings_changed).pack(side='right')
        options = tk.Frame(settings, bg=self.CARD)
        options.pack(fill='x', pady=(10, 0))
        self._segment(options, self._enhancement_rounds_var,
                      [('1 次', '1'), ('2 次', '2'), ('3 次', '3')],
                      self._on_enhancement_settings_changed).pack(side='left')
        self._label(options, '任意红色词条或无第三种颜色 → 保留',
                    fg=self.MUTED).pack(side='left', padx=(22, 0))

        metrics = self._panel(outer, '')
        for i, (title, key) in enumerate((('已分解', 'total_decomposed'), ('已保留', 'total_kept'), ('强化次数', 'enhancement_clicks'))):
            cell = tk.Frame(metrics, bg=self.CARD)
            cell.grid(row=0, column=i, sticky='ew')
            metrics.columnconfigure(i, weight=1, uniform='metric')
            var = tk.StringVar(value='0')
            self._vars[key] = var
            self._label(cell, '', textvariable=var, size=25, bold=True, fg='#007aff').pack(anchor='w')
            self._label(cell, title, fg=self.MUTED).pack(anchor='w')

        current = self._panel(outer, '')
        self._vars['current_combination'] = tk.StringVar(value='—')
        self._label(current, '当前刻印', fg=self.MUTED).pack(anchor='w')
        self._label(current, '', textvariable=self._vars['current_combination'], size=13, bold=True).pack(anchor='w', pady=(3, 0))
        self._vars['last_action'] = tk.StringVar(value='等待选卡')
        action = self._label(current, '', textvariable=self._vars['last_action'], fg=self.MUTED, anchor='w', justify='left')
        action.pack(fill='x', pady=(4, 0))
        action.bind('<Configure>', lambda e: action.configure(wraplength=max(200, e.width-8)))

        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        stats = tk.Frame(tabs, bg=self.CARD, padx=14, pady=12)
        detail = tk.Frame(tabs, bg=self.CARD, padx=14, pady=12)
        history = tk.Frame(tabs, bg=self.CARD, padx=14, pady=12)
        tabs.add(stats, text='颜色统计')
        tabs.add(detail, text='运行详情')
        tabs.add(history, text='操作记录')
        strip = tk.Frame(stats, bg=self.CARD)
        strip.pack(fill='x', pady=(0, 12))
        for element in ELEMENT_ORDER:
            var = tk.StringVar(value='0')
            self._element_vars[element] = var
            cell = tk.Frame(strip, bg=self.CARD)
            cell.pack(side='left', expand=True)
            try:
                icon = self._element_icon(element)
                self._element_images[element] = icon
                tk.Label(cell, image=icon, bg=self.CARD).pack(side='left')
            except (FileNotFoundError, OSError, KeyError) as exc:
                self._label(cell, element, fg=self.MUTED).pack(side='left')
            self._label(cell, '', textvariable=var, size=14, bold=True).pack(side='left', padx=(4, 0))
        self._combination_text = self._text(stats)
        self._history = self._text(history)
        for label, key in (('窗口', 'window_status'), ('识别', 'vision_state'), ('元素数', 'current_filled_slots'),
                           ('确认', 'confirmation_mode'), ('强化判断', 'enhancement_status'), ('最近错误', 'last_error')):
            row = tk.Frame(detail, bg=self.CARD)
            row.pack(fill='x', pady=3)
            self._label(row, label, fg=self.MUTED, width=9, anchor='w').pack(side='left')
            var = tk.StringVar(value='—')
            self._vars[key] = var
            self._label(row, '', textvariable=var, anchor='w', wraplength=450, justify='left').pack(side='left', fill='x', expand=True)

    def _text(self, parent):
        scroll = ttk.Scrollbar(parent)
        scroll.pack(side='right', fill='y')
        widget = tk.Text(parent, height=3, bg=self.CARD, fg=self.TEXT, relief='flat',
                         font=('Microsoft YaHei UI', 10), wrap='word', state='disabled',
                         highlightthickness=0, padx=4, pady=4, yscrollcommand=scroll.set)
        widget.pack(fill='both', expand=True)
        scroll.configure(command=widget.yview)
        return widget
