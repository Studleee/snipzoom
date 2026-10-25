"""Flat toolbar buttons and hover tooltips."""

from __future__ import annotations

import tkinter as tk

from .theme import ACCENT, BAR, FONT, FONT_SM, TEXT


class FlatButton(tk.Frame):
    def __init__(self, parent, text: str, command, *, accent: bool = False, tooltip: str = "", width: int | None = None):
        super().__init__(parent, bg=BAR, cursor="hand2", highlightthickness=0, bd=0)
        self.command = command
        self.accent = accent
        self.selected = False
        self.enabled = True
        self._hover = False
        self._pressed = False
        self._tip = None
        self._tip_job = None
        self._tooltip = tooltip
        self.label = tk.Label(
            self,
            text=text,
            font=FONT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            width=width or 0,
            anchor="w" if width else "center",
        )
        self.label.pack()
        for widget in (self, self.label):
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<ButtonRelease-1>", self._release)
        self._paint()

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        cursor = "hand2" if enabled else "arrow"
        self.configure(cursor=cursor)
        self.label.configure(cursor=cursor)
        if not enabled:
            self._hover = False
            self._pressed = False
            self._hide_tip()
        self._paint()

    def _contains(self, widget) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _enter(self, _event=None) -> None:
        if not self.enabled:
            return
        self._hover = True
        self._paint()
        self._schedule_tip()

    def _leave(self, _event=None) -> None:
        target = self.winfo_containing(*self.winfo_pointerxy())
        if self._contains(target):
            return
        self._hover = False
        self._pressed = False
        self._paint()
        self._hide_tip()

    def _press(self, _event=None) -> None:
        if not self.enabled:
            return
        self._pressed = True
        self._hide_tip()
        self._paint()

    def _release(self, event) -> None:
        if not self._pressed:
            return
        self._pressed = False
        self._paint()
        if not self.enabled:
            return
        target = self.winfo_containing(event.x_root, event.y_root)
        if self._contains(target):
            self.command()

    def _colors(self) -> tuple[str, str]:
        if not self.enabled:
            return "#3a3a3a", "#8d8d8d"
        if self.accent:
            if self._pressed:
                return "#0c6e96", "#ffffff"
            if self._hover:
                return "#1aa0db", "#ffffff"
            return "#0e8ec4", "#ffffff"
        if self.selected:
            if self._pressed or self._hover:
                return "#1494c4", TEXT
            return "#0d6f96", TEXT
        if self._pressed:
            return "#303030", TEXT
        if self._hover:
            return "#4a4a4a", TEXT
        return "#3a3a3a", TEXT

    def _paint(self) -> None:
        bg, fg = self._colors()
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg)

    def _schedule_tip(self) -> None:
        self._hide_tip()
        if self._tooltip and self.enabled:
            self._tip_job = self.after(480, self._show_tip)

    def _show_tip(self) -> None:
        self._tip_job = None
        if not self._hover or not self._tooltip:
            return
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            tip,
            text=self._tooltip,
            bg="#111111",
            fg=TEXT,
            font=FONT_SM,
            padx=8,
            pady=4,
            highlightthickness=1,
            highlightbackground=ACCENT,
        )
        label.pack()
        tip.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 6
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip_job is not None:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class ColorSwatch(tk.Frame):
    def __init__(self, parent, color: str, command, size: int = 18):
        super().__init__(
            parent,
            width=size,
            height=size,
            bg=color,
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=BAR,
            bd=0,
        )
        self.pack_propagate(False)
        self.color = color
        self.command = command
        self.enabled = True
        self.bind("<Button-1>", self._click)

    def _click(self, _event=None) -> None:
        if self.enabled:
            self.command(self.color)

    def set_selected(self, selected: bool) -> None:
        self.configure(highlightbackground="#ffffff" if selected else BAR)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
