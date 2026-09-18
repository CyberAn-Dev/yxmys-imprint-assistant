from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from . import __version__
from .controller import ELEMENT_ORDER, ImprintDecomposeController
from .models import ImprintStats


class RoundedButton(tk.Canvas):
    """Small native Tk canvas button with an Apple-like rounded shape."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command,
        width: int,
        bg: str,
        hover_bg: str,
        fg: str,
        font: tuple[str, int, str] = ("Segoe UI", 11, "bold"),
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=38,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._label = text
        self._command = command
        self._normal = bg
        self._hover = hover_bg
        self._fg = fg
        self._font = font
        self._draw(self._normal)
        self.bind("<Enter>", lambda _event: self._draw(self._hover))
        self.bind("<Leave>", lambda _event: self._draw(self._normal))
        self.bind("<Button-1>", self._invoke)

    def _invoke(self, _event=None) -> None:
        if self._command:
            self._command()

    def _draw(self, fill: str) -> None:
        self.delete("all")
        width = max(20, int(self.cget("width")))
        height = max(20, int(self.cget("height")))
        radius = 10
        self.create_rectangle(radius, 1, width - radius, height - 1, fill=fill, outline=fill)
        self.create_rectangle(1, radius, width - 1, height - radius, fill=fill, outline=fill)
        self.create_arc(1, 1, radius * 2 + 1, radius * 2 + 1, start=90, extent=90, fill=fill, outline=fill)
        self.create_arc(width - radius * 2 - 1, 1, width - 1, radius * 2 + 1, start=0, extent=90, fill=fill, outline=fill)
        self.create_arc(1, height - radius * 2 - 1, radius * 2 + 1, height - 1, start=180, extent=90, fill=fill, outline=fill)
        self.create_arc(width - radius * 2 - 1, height - radius * 2 - 1, width - 1, height - 1, start=270, extent=90, fill=fill, outline=fill)
        self.create_text(width // 2, height // 2, text=self._label, fill=self._fg, font=self._font)


class AppleCard(tk.Frame):
    """Rounded dark surface that hosts normal Tk controls."""

    def __init__(self, parent: tk.Misc, *, height: int, bg: str, surface: str) -> None:
        super().__init__(parent, bg=bg, height=height, highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self._surface = surface
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, height=height)
        self._canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self._canvas, bg=surface, bd=0, highlightthickness=0)
        self._window = self._canvas.create_window((3, 3), window=self.body, anchor="nw", width=1, height=max(1, height - 6))
        self._canvas.bind("<Configure>", self._resize)

    def _resize(self, event) -> None:
        width = max(1, event.width)
        height = max(1, event.height)
        self._canvas.coords(self._window, 3, 3)
        self._canvas.itemconfigure(self._window, width=max(1, width - 6), height=max(1, height - 6))
        self._canvas.delete("card-shape")
        radius = 14
        item = self._canvas.create_rectangle(radius, 1, width - radius, height - 1, fill=self._surface, outline="#d1d1d6", width=1, tags="card-shape")
        self._canvas.create_rectangle(1, radius, width - 1, height - radius, fill=self._surface, outline="", tags="card-shape")
        for box, start in (
            ((1, 1, radius * 2 + 1, radius * 2 + 1), 90),
            ((width - radius * 2 - 1, 1, width - 1, radius * 2 + 1), 0),
            ((1, height - radius * 2 - 1, radius * 2 + 1, height - 1), 180),
            ((width - radius * 2 - 1, height - radius * 2 - 1, width - 1, height - 1), 270),
        ):
            self._canvas.create_arc(box, start=start, extent=90, fill=self._surface, outline="", tags="card-shape")
        self._canvas.tag_lower(item, self._window)


class ImprintDecomposeUI:
    """Full original control panel with a white-and-blue Apple-inspired visual layer."""

    BG = "#f2f2f7"
    CARD = "#ffffff"
    CARD_ALT = "#f5f5f7"
    FIELD = "#f2f2f7"
    TEXT = "#1d1d1f"
    MUTED = "#6e6e73"
    BORDER = "#d1d1d6"
    BLUE = "#007aff"
    BLUE_HOVER = "#409cff"
    RED = "#ff3b30"
    RED_HOVER = "#ff6961"

    def __init__(self, controller: ImprintDecomposeController) -> None:
        self.controller = controller
        self.root = tk.Tk()
        self.root.title(f"刻印分解 · {__version__}")
        self.root.geometry("820x900")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        try:
            self.root.attributes("-alpha", 0.98)
        except tk.TclError:
            pass
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(700, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

        self._vars: dict[str, tk.StringVar] = {}
        self._element_vars: dict[str, tk.StringVar] = {}
        self._enhancement_enabled_var = tk.BooleanVar(value=False)
        self._enhancement_rounds_var = tk.StringVar(value="1")
        self._enhancement_threshold_var = tk.StringVar(value="20")
        self._confirmation_mode_var = tk.StringVar(value="auto")
        self._build()
        self.controller.on_stats = self._on_stats_threadsafe
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    def _label(self, parent: tk.Misc, text: str, *, fg=None, size=9, bold=False, **kwargs):
        return tk.Label(
            parent, text=text, bg=kwargs.pop("bg", self.CARD), fg=fg or self.TEXT,
            font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs,
        )

    def _radio(self, parent, text, value, variable, command):
        return tk.Radiobutton(
            parent, text=text, value=value, variable=variable, command=command,
            bg=self.CARD, fg=self.TEXT, activebackground=self.CARD,
            activeforeground=self.TEXT, selectcolor=self.FIELD,
            font=("Segoe UI", 9), highlightthickness=0, bd=0,
        )

    def _check(self, parent, text, variable, command):
        return tk.Checkbutton(
            parent, text=text, variable=variable, command=command,
            bg=self.CARD, fg=self.TEXT, activebackground=self.CARD,
            activeforeground=self.TEXT, selectcolor=self.FIELD,
            font=("Segoe UI", 9), highlightthickness=0, bd=0,
        )

    def _card(self, parent, height: int) -> AppleCard:
        card = AppleCard(parent, height=height, bg=self.BG, surface=self.CARD)
        card.pack(fill="x", pady=(0, 7))
        return card

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        header = tk.Frame(outer, bg=self.BG, height=45)
        header.pack(fill="x", pady=(0, 6))
        header.pack_propagate(False)
        self._label(header, "刻印分解", bg=self.BG, size=21, bold=True).pack(side="left", anchor="w")
        self._label(header, f"  原版完整控制面板  ·  v{__version__}", bg=self.BG, fg=self.MUTED, size=11).pack(side="left", anchor="w", padx=(4, 0), pady=(6, 0))
        self._label(header, "视觉识别 · 快速分解", bg=self.BG, fg=self.MUTED, size=10).pack(side="right", anchor="e", pady=(7, 0))

        buttons = tk.Frame(outer, bg=self.BG, height=42)
        buttons.pack(fill="x", pady=(0, 6))
        buttons.pack_propagate(False)
        RoundedButton(buttons, text="开始  F9", command=self.controller.start, width=120, bg=self.BLUE, hover_bg=self.BLUE_HOVER, fg="white").pack(side="left", padx=(0, 8))
        RoundedButton(buttons, text="暂停", command=self.controller.pause, width=100, bg="#e5e5ea", hover_bg="#d1d1d6", fg=self.TEXT).pack(side="left", padx=(0, 8))
        RoundedButton(buttons, text="停止  F10", command=self.controller.stop, width=120, bg=self.RED, hover_bg=self.RED_HOVER, fg="white").pack(side="left")

        manual = self._card(outer, 58)
        self._label(manual.body, "手动处理", bg=self.CARD, size=11, bold=True).pack(anchor="w", padx=16, pady=(8, 0))
        self._label(manual.body, "打开刻印列表后，手动点击要处理的刻印；程序只处理当前打开的卡片。", bg=self.CARD, fg=self.MUTED, size=10).pack(anchor="w", padx=16)

        confirm = self._card(outer, 62)
        self._label(confirm.body, "分解确认方式", bg=self.CARD, size=11, bold=True).pack(anchor="w", padx=16, pady=(7, 0))
        row = tk.Frame(confirm.body, bg=self.CARD)
        row.pack(fill="x", padx=12, pady=(1, 0))
        self._radio(row, "自动确认（敏感情况暂停）", "auto", self._confirmation_mode_var, self._on_confirmation_mode_changed).pack(side="left")
        self._radio(row, "始终手动确认", "manual", self._confirmation_mode_var, self._on_confirmation_mode_changed).pack(side="left", padx=(28, 0))

        enhance = self._card(outer, 128)
        self._label(enhance.body, "强化模块（默认关闭）", bg=self.CARD, size=11, bold=True).grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(7, 2))
        self._check(enhance.body, "启用：对不需要的刻印先强化，再判断红色属性", self._enhancement_enabled_var, self._on_enhancement_settings_changed).grid(row=1, column=0, columnspan=6, sticky="w", padx=12, pady=(0, 2))
        self._label(enhance.body, "强化次数", bg=self.CARD, fg=self.MUTED, size=9).grid(row=2, column=0, sticky="w", padx=(16, 4), pady=(5, 0))
        rounds = tk.Frame(enhance.body, bg=self.CARD)
        rounds.grid(row=2, column=1, columnspan=3, sticky="w", pady=(5, 0))
        for value in ("1", "2", "3"):
            self._radio(rounds, f"{value} 次", value, self._enhancement_rounds_var, self._on_enhancement_settings_changed).pack(side="left", padx=(0, 14))
        self._label(enhance.body, "红色属性阈值", bg=self.CARD, fg=self.MUTED, size=9).grid(row=2, column=4, sticky="e", padx=(8, 6), pady=(5, 0))
        threshold = tk.Entry(enhance.body, width=6, textvariable=self._enhancement_threshold_var, justify="center", bg=self.FIELD, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", highlightthickness=1, highlightbackground=self.BORDER, highlightcolor=self.BLUE, font=("Segoe UI", 9))
        threshold.grid(row=2, column=5, sticky="w", padx=(0, 16), pady=(5, 0))
        threshold.bind("<Return>", lambda _event: self._on_enhancement_settings_changed())
        threshold.bind("<FocusOut>", lambda _event: self._on_enhancement_settings_changed(show_error=False))
        self._label(enhance.body, "固定强化 1/2/3 次；红色百分比 > 阈值则保留，否则分解。强化 2 次新增元素仍属于原始元素时也保留。", bg=self.CARD, fg=self.MUTED, size=9).grid(row=3, column=0, columnspan=6, sticky="w", padx=16, pady=(5, 0))
        for col in range(6):
            enhance.body.grid_columnconfigure(col, weight=1 if col in (1, 2, 3) else 0)

        status = self._card(outer, 178)
        self._label(status.body, "状态与关键统计", bg=self.CARD, size=11, bold=True).pack(anchor="w", padx=16, pady=(7, 2))
        grid = tk.Frame(status.body, bg=self.CARD)
        grid.pack(fill="x", padx=16)
        rows = (
            (("程序状态", "program_status"), ("窗口状态", "window_status")),
            (("视觉状态", "vision_state"), ("当前填槽", "current_filled_slots")),
            (("当前右卡组合", "current_combination"), ("确认方式", "confirmation_mode")),
            (("强化保留判断", "enhancement_status"), ("已分解", "total_decomposed")),
            (("已保留", "total_kept"), ("强化点击", "enhancement_clicks")),
            (("最近动作", "last_action"), ("最近错误", "last_error")),
        )
        for row_index, row_items in enumerate(rows):
            for column, (label, key) in enumerate(row_items):
                self._label(grid, label, bg=self.CARD, fg=self.MUTED, size=9).grid(row=row_index, column=column * 2, sticky="w", pady=2)
                var = tk.StringVar(value="-")
                self._vars[key] = var
                self._label(grid, "", bg=self.CARD, fg=self.TEXT, size=10, bold=True, textvariable=var).grid(row=row_index, column=column * 2 + 1, sticky="w", padx=(8, 32), pady=2)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        stats = self._card(outer, 120)
        self._label(stats.body, "统计（确认成功后计入）", bg=self.CARD, size=11, bold=True).pack(anchor="w", padx=16, pady=(7, 2))
        element_row = tk.Frame(stats.body, bg=self.CARD)
        element_row.pack(fill="x", padx=12, pady=(0, 5))
        for element in ELEMENT_ORDER:
            var = tk.StringVar(value=f"{element}: 0")
            self._element_vars[element] = var
            tk.Label(element_row, textvariable=var, bg=self.CARD_ALT, fg=self.TEXT, font=("Segoe UI", 9), padx=10, pady=4).pack(side="left", fill="x", expand=True, padx=3)
        self._label(stats.body, "颜色组合", bg=self.CARD, fg=self.MUTED, size=9).pack(anchor="w", padx=16)
        self._combination_text = tk.Text(stats.body, height=2, bg=self.FIELD, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", borderwidth=0, highlightthickness=0, font=("Consolas", 9), padx=8, pady=3, state="disabled")
        self._combination_text.pack(fill="x", padx=16, pady=(2, 0))

        history = self._card(outer, 138)
        self._label(history.body, "操作记录（最近 20 条）", bg=self.CARD, size=11, bold=True).pack(anchor="w", padx=16, pady=(7, 2))
        history_body = tk.Frame(history.body, bg=self.CARD)
        history_body.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        scrollbar = tk.Scrollbar(history_body, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self._history = tk.Text(history_body, height=5, bg=self.FIELD, fg="#3a3a3c", insertbackground=self.TEXT, relief="flat", borderwidth=0, highlightthickness=0, font=("Consolas", 9), padx=8, pady=3, state="disabled", yscrollcommand=scrollbar.set)
        self._history.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._history.yview)

        self._label(outer, "先打开刻印总界面，再按 F9 开始；F10 停止流程，F11 调试。日志：logs/imprint_decompose.log", bg=self.BG, fg=self.MUTED, size=9).pack(anchor="w")

    def _on_confirmation_mode_changed(self) -> None:
        try:
            self.controller.set_confirmation_mode(self._confirmation_mode_var.get())
        except (TypeError, ValueError) as exc:
            messagebox.showerror("确认方式无效", str(exc), parent=self.root)

    def _on_enhancement_settings_changed(self, _event=None, *, show_error=True):
        try:
            rounds = int(self._enhancement_rounds_var.get())
            threshold = float(self._enhancement_threshold_var.get())
            self.controller.set_enhancement_settings(self._enhancement_enabled_var.get(), rounds, threshold)
        except (TypeError, ValueError) as exc:
            if show_error:
                messagebox.showerror("强化设置无效", str(exc), parent=self.root)
        return "break" if _event is not None else None

    def _on_stats_threadsafe(self, stats: ImprintStats) -> None:
        self.root.after(0, lambda: self._apply_stats(stats))

    def _apply_stats(self, stats: ImprintStats) -> None:
        mapping = {
            "program_status": stats.program_status,
            "window_status": stats.window_status,
            "vision_state": stats.vision_state,
            "current_combination": stats.current_combination,
            "current_filled_slots": stats.current_filled_slots,
            "confirmation_mode": stats.confirmation_mode,
            "enhancement_status": stats.enhancement_status,
            "total_decomposed": str(stats.total_decomposed),
            "total_kept": str(stats.total_kept),
            "enhancement_clicks": str(stats.enhancement_clicks),
            "last_action": stats.last_action,
            "last_error": stats.last_error,
        }
        for key, value in mapping.items():
            self._vars[key].set(value or "-")
        for element in ELEMENT_ORDER:
            self._element_vars[element].set(f"{element}: {stats.element_counts.get(element, 0)}")
        self._enhancement_enabled_var.set(stats.enhancement_enabled)
        self._enhancement_rounds_var.set(str(stats.enhancement_target))
        self._confirmation_mode_var.set("manual" if stats.confirmation_mode == "手动确认" else "auto")

        self._combination_text.configure(state="normal")
        self._combination_text.delete("1.0", "end")
        lines = [f"{label}  ×  {count}" for label, count in stats.combination_counts.items()]
        self._combination_text.insert("1.0", "\n".join(lines) or "-")
        self._combination_text.configure(state="disabled")

        self._history.configure(state="normal")
        self._history.delete("1.0", "end")
        self._history.insert("1.0", stats.operation_history or "-")
        self._history.see("end")
        self._history.configure(state="disabled")

    def _poll(self) -> None:
        if self.controller.stopping.is_set():
            self.root.quit()
            return
        try:
            self.controller.refresh_window_info()
        except Exception:
            pass
        self.root.after(350, self._poll)

    def _on_close(self) -> None:
        self.controller.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
