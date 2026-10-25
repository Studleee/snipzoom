"""SnipZoom window: snip, then zoom."""

from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageOps, ImageTk

from .editor import Editor
from .overlay import SnipOverlay
from .screen import capture_screen, copy_image, top_level_hwnd, work_area
from .screen import user32
from .theme import (
    ACCENT,
    ACCENT_BUTTON,
    BAR,
    BG,
    FONT,
    FONT_BODY,
    FONT_SM,
    FONT_TITLE,
    HAIRLINE,
    MARKER_COLORS,
    MUTED,
    PEN_COLORS,
    STATUS,
    TEXT,
    build_icon,
    enable_dark_titlebar,
    ui_scale,
)
from .widgets import ColorSwatch, FlatButton

HOTKEY_ID = 0x5A10
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_S = 0x53
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

DELAYS = ((0, "No delay"), (3, "Wait 3s"), (5, "Wait 5s"))


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


user32.PeekMessageW.argtypes = [
    ctypes.POINTER(MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL


class Countdown:
    def __init__(self, root: tk.Tk, seconds: int, on_done, on_cancel):
        self.seconds = seconds
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.cancelled = False
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#1b1b1b", highlightthickness=1, highlightbackground=ACCENT)
        self.label = tk.Label(
            self.top,
            text=str(seconds),
            font=("Segoe UI", 48),
            fg="white",
            bg="#1b1b1b",
        )
        self.label.pack(padx=42, pady=(18, 0))
        tk.Label(
            self.top,
            text="Snipping soon — Esc cancels",
            font=FONT,
            fg=MUTED,
            bg="#1b1b1b",
        ).pack(padx=28, pady=(0, 18))
        self.top.bind("<Escape>", lambda _e: self.cancel())
        self.top.update_idletasks()
        width = self.top.winfo_reqwidth()
        height = self.top.winfo_reqheight()
        left, top, right, bottom = work_area()
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
        self.top.geometry(f"{width}x{height}+{x}+{y}")
        self.top.lift()
        self.top.focus_force()
        self._tick()

    def _tick(self) -> None:
        if self.cancelled:
            return
        if self.seconds <= 0:
            try:
                self.top.destroy()
            except tk.TclError:
                pass
            self.on_done()
            return
        self.label.configure(text=str(self.seconds))
        self.seconds -= 1
        self.top.after(1000, self._tick)

    def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        self.on_cancel()


class SnipZoomApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SnipZoom")
        self.root.configure(bg=BG)
        self.scale = ui_scale(self.root)
        self.delay_seconds = 0
        self.pinned = False
        self._busy = False
        self._dialog = False
        self._closing = False
        self.overlay: SnipOverlay | None = None
        self.countdown: Countdown | None = None
        self._hwnd = 0
        self._hotkey_on = False
        self._flash_gen = 0
        self._hint_text = "Ctrl+Shift+S snips from anywhere"
        self._size_text = ""
        self._pointer_text = ""
        self._tool = "pan"
        self._pen_color = PEN_COLORS[0]
        self._marker_color = MARKER_COLORS[0]

        self._build()
        self._place_window()
        enable_dark_titlebar(self.root)
        icon = build_icon(64)
        self._icon = ImageTk.PhotoImage(icon)
        self.root.iconphoto(True, self._icon)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind("<Control-c>", self._copy_event)
        self.root.bind("<Control-s>", self._save_event)
        self.root.bind("<Control-z>", self._undo_event)
        self.root.bind("<Control-y>", self._redo_event)
        self.root.bind("<Control-Z>", self._redo_event)
        self.root.bind("<Control-Shift-Z>", self._redo_event)
        self.root.bind("<Control-Shift-s>", lambda _e: self.start_snip())
        self.root.bind("<Control-Shift-S>", lambda _e: self.start_snip())
        self.root.bind("<Control-0>", lambda _e: self.editor.fit())
        self.root.bind("<Control-1>", lambda _e: self.editor.actual())
        self.root.bind("<Control-plus>", lambda _e: self.editor.zoom_in())
        self.root.bind("<Control-equal>", lambda _e: self.editor.zoom_in())
        self.root.bind("<Control-minus>", lambda _e: self.editor.zoom_out())
        self.root.bind("<KeyPress-space>", self._space_down)
        self.root.bind("<KeyRelease-space>", self._space_up)
        self.root.bind("<Escape>", self._on_escape)
        self.root.after(200, self._register_hotkey)
        self.root.after(250, self._poll_hotkey)
        self._sync_buttons()
        self._set_hint()

    def _build(self) -> None:
        tk.Frame(self.root, bg=ACCENT, height=3).pack(side="top", fill="x")

        actions = tk.Frame(self.root, bg=BAR)
        actions.pack(side="top", fill="x")
        row = tk.Frame(actions, bg=BAR)
        row.pack(fill="x", padx=10, pady=8)

        self.snip_btn = FlatButton(row, "New snip", self.start_snip, accent=True, tooltip="Select a region (Ctrl+Shift+S)")
        self.snip_btn.pack(side="left")
        self.delay_btn = FlatButton(
            row,
            "No delay  ▾",
            self._open_delay_menu,
            tooltip="How long to wait before the snip",
            width=14,
        )
        self.delay_btn.pack(side="left", padx=(8, 0))
        self.delay_menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#2a2a2a",
            fg=TEXT,
            activebackground=ACCENT_BUTTON,
            activeforeground="#ffffff",
            font=FONT,
            bd=0,
            relief="flat",
        )
        self.open_btn = FlatButton(row, "Open", self.open_image, tooltip="Open an image and zoom it")
        self.open_btn.pack(side="left", padx=(8, 0))
        self._sep(row)
        self.copy_btn = FlatButton(row, "Copy", self.copy_image, tooltip="Copy to the clipboard (Ctrl+C)")
        self.copy_btn.pack(side="left")
        self.save_btn = FlatButton(row, "Save", self.save_image, tooltip="Save a PNG or JPEG (Ctrl+S)")
        self.save_btn.pack(side="left", padx=(8, 0))
        self.pin_btn = FlatButton(row, "Pin", self.toggle_pin, tooltip="Keep this window above other windows")
        self.pin_btn.pack(side="left", padx=(8, 0))

        tk.Frame(row, bg=BAR).pack(side="left", fill="x", expand=True)
        self.detail_btn = FlatButton(
            row,
            "Detail",
            self.toggle_detail,
            tooltip="Divide each pixel for watch parts and photos. Click again for hard pixels.",
        )
        self.detail_btn.pack(side="left", padx=(0, 8))
        self.zoom_out_btn = FlatButton(row, "−", self.editor_zoom_out, tooltip="Zoom out")
        self.zoom_out_btn.pack(side="left")
        self.percent = tk.Label(
            row,
            text="—",
            width=6,
            bg=BAR,
            fg=TEXT,
            font=FONT,
            cursor="hand2",
        )
        self.percent.pack(side="left", padx=4)
        self.percent.bind("<Button-1>", lambda _e: self.editor.actual())
        self.zoom_in_btn = FlatButton(row, "+", self.editor_zoom_in, tooltip="Zoom in")
        self.zoom_in_btn.pack(side="left")
        self.fit_btn = FlatButton(
            row,
            "Fit",
            lambda: self.editor.fit(),
            tooltip="Fit in the window (Ctrl+0). Click the percent for 100%.",
        )
        self.fit_btn.pack(side="left", padx=(8, 0))

        tk.Frame(self.root, bg=HAIRLINE, height=1).pack(side="top", fill="x")

        tools = tk.Frame(self.root, bg=BAR)
        tools.pack(side="top", fill="x")
        tool_row = tk.Frame(tools, bg=BAR)
        tool_row.pack(fill="x", padx=10, pady=8)

        self.tool_buttons = {}
        for key, label, tip in (
            ("pan", "Move", "Drag to pan. Double-click toggles 100%."),
            ("pen", "Pen", "Draw on the snip"),
            ("marker", "Highlight", "Translucent marker"),
            ("eraser", "Erase", "Erase marks"),
        ):
            button = FlatButton(tool_row, label, lambda k=key: self.set_tool(k), tooltip=tip)
            button.pack(side="left", padx=(0, 8))
            self.tool_buttons[key] = button

        self._sep(tool_row)
        self.undo_btn = FlatButton(tool_row, "Undo", self.editor_undo, tooltip="Undo (Ctrl+Z)")
        self.undo_btn.pack(side="left")
        self.redo_btn = FlatButton(tool_row, "Redo", self.editor_redo, tooltip="Redo (Ctrl+Y)")
        self.redo_btn.pack(side="left", padx=(8, 0))
        self._sep(tool_row)

        self.swatch_row = tk.Frame(tool_row, bg=BAR)
        self.swatch_row.pack(side="left")
        self.swatches: list[ColorSwatch] = []
        self._rebuild_swatches()

        self.status = tk.Frame(self.root, bg=STATUS)
        self.status.pack(side="bottom", fill="x")
        tk.Frame(self.status, bg=HAIRLINE, height=1).pack(side="top", fill="x")
        body = tk.Frame(self.status, bg=STATUS)
        body.pack(fill="both", expand=True)
        self.hint = tk.Label(body, text=self._hint_text, bg=STATUS, fg=MUTED, font=FONT_SM, anchor="w")
        self.hint.pack(side="left", padx=12)
        self.detail = tk.Label(body, text="", bg=STATUS, fg=MUTED, font=FONT_SM, anchor="e")
        self.detail.pack(side="right", padx=(0, 12))
        self.chip = tk.Canvas(
            body,
            width=14,
            height=14,
            bg=STATUS,
            highlightthickness=1,
            highlightbackground=STATUS,
            bd=0,
        )
        self.chip.pack(side="right", padx=(0, 8))

        self.stage = tk.Frame(self.root, bg=BG)
        self.stage.pack(side="top", fill="both", expand=True)
        self.stage.rowconfigure(0, weight=1)
        self.stage.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.stage, bg="#101010", highlightthickness=0, bd=0, cursor="arrow")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.editor = Editor(self.canvas, self._on_view_change, self._on_pointer, self._sync_buttons)
        self.canvas.grid_remove()

        self.empty = tk.Frame(self.stage, bg=BG)
        self.empty.grid(row=0, column=0)
        inner = tk.Frame(self.empty, bg=BG)
        inner.pack(padx=32, pady=28)
        badge = tk.Canvas(inner, width=84, height=84, bg=BG, highlightthickness=0)
        badge.pack(pady=(8, 16))
        badge.create_oval(6, 6, 78, 78, outline=ACCENT, width=3)
        badge.create_rectangle(22, 26, 58, 54, outline=TEXT, width=2)
        badge.create_line(56, 52, 72, 68, fill=ACCENT, width=4, capstyle="round")
        tk.Label(inner, text="Snip, then zoom", bg=BG, fg=TEXT, font=FONT_TITLE).pack()
        tk.Label(
            inner,
            text=(
                "Drag a region the way Snipping Tool does.\n"
                "A magnifier follows the cursor while you drag.\n"
                "Afterward, scroll to zoom in and drag to look around."
            ),
            bg=BG,
            fg=MUTED,
            font=FONT_BODY,
            justify="center",
        ).pack(pady=(8, 18))
        FlatButton(inner, "New snip", self.start_snip, accent=True, tooltip="Ctrl+Shift+S").pack()
        tk.Label(inner, text="Shortcut   Ctrl+Shift+S", bg=BG, fg=MUTED, font=FONT_SM).pack(pady=(12, 0))

    def _sep(self, parent) -> None:
        line = tk.Frame(parent, width=1, height=max(22, int(22 * self.scale)), bg="#5a5a5a")
        line.pack(side="left", padx=10)
        line.pack_propagate(False)

    def _place_window(self) -> None:
        left, top, right, bottom = work_area()
        area_w = max(1, right - left)
        area_h = max(1, bottom - top)
        width = min(int(1120 * self.scale), int(area_w * 0.88))
        height = min(int(740 * self.scale), int(area_h * 0.88))
        width = max(width, min(int(860 * self.scale), area_w - 40))
        height = max(height, min(int(560 * self.scale), area_h - 40))
        x = left + max(0, (area_w - width) // 2)
        y = top + max(0, (area_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(width, int(820 * self.scale)), min(height, int(520 * self.scale)))

    def _rebuild_swatches(self) -> None:
        for swatch in self.swatches:
            swatch.destroy()
        self.swatches.clear()
        colors = MARKER_COLORS if self._tool == "marker" else PEN_COLORS
        current = self._marker_color if self._tool == "marker" else self._pen_color
        size = max(16, int(16 * self.scale))
        for color in colors:
            swatch = ColorSwatch(self.swatch_row, color, self._pick_color, size=size)
            swatch.pack(side="left", padx=3)
            swatch.set_selected(color.lower() == current.lower())
            self.swatches.append(swatch)

    def _pick_color(self, color: str) -> None:
        if not self.editor.has_image():
            return
        if self._tool == "marker":
            self._marker_color = color
            self.editor.set_marker_color(color)
        else:
            self._pen_color = color
            self.editor.set_pen_color(color)
            if self._tool != "pen":
                self.set_tool("pen")
                return
        for swatch in self.swatches:
            swatch.set_selected(swatch.color.lower() == color.lower())

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self.editor.set_tool(tool)
        for key, button in self.tool_buttons.items():
            button.set_selected(key == tool)
        self._rebuild_swatches()
        enabled = tool in ("pen", "marker")
        for swatch in self.swatches:
            swatch.set_enabled(enabled)
        self._set_hint()

    def _open_delay_menu(self) -> None:
        self.delay_menu.delete(0, "end")
        for seconds, label in DELAYS:
            mark = "✓   " if seconds == self.delay_seconds else "     "
            self.delay_menu.add_command(
                label=mark + label,
                command=lambda choice=seconds: self._set_delay(choice),
            )
        x = self.delay_btn.winfo_rootx()
        y = self.delay_btn.winfo_rooty() + self.delay_btn.winfo_height()
        try:
            self.delay_menu.tk_popup(x, y)
        finally:
            self.delay_menu.grab_release()

    def _set_delay(self, seconds: int) -> None:
        self.delay_seconds = seconds
        label = dict(DELAYS)[seconds]
        self.delay_btn.set_text(f"{label}  ▾")

    def toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_btn.set_selected(self.pinned)
        self.pin_btn.set_text("Pinned" if self.pinned else "Pin")

    def start_snip(self) -> None:
        if self._busy or self._dialog or self._closing:
            return
        self._busy = True
        self.root.withdraw()
        if self.delay_seconds > 0:
            self.countdown = Countdown(
                self.root,
                self.delay_seconds,
                self._countdown_finished,
                self._countdown_cancelled,
            )
        else:
            self.root.after(160, self._capture_and_overlay)

    def _countdown_finished(self) -> None:
        self.countdown = None
        if self._closing:
            return
        self.root.after(140, self._capture_and_overlay)

    def _countdown_cancelled(self) -> None:
        self.countdown = None
        if self._closing:
            return
        self._busy = False
        self._restore_window()
        self.flash("Snip cancelled")

    def _capture_and_overlay(self) -> None:
        if self._closing:
            return
        try:
            shot = capture_screen()
        except Exception as exc:
            self._busy = False
            self._restore_window()
            messagebox.showerror("SnipZoom", f"Couldn't capture the screen.\n\n{exc}")
            return
        self.overlay = SnipOverlay(self.root, shot, self._snip_done, self._snip_cancel)

    def _snip_done(self, image: Image.Image) -> None:
        self.overlay = None
        self._busy = False
        if self._closing:
            return
        self._restore_window()
        self._show_image(image)
        try:
            copy_image(self.editor.flattened())
            self.flash("Copied to clipboard")
        except Exception as exc:
            self.flash(f"Snip ready — clipboard failed ({exc})")

    def _snip_cancel(self) -> None:
        self.overlay = None
        self._busy = False
        if self._closing:
            return
        self._restore_window()
        self.flash("Snip cancelled")

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self.pinned:
            self.root.attributes("-topmost", True)

    def _show_image(self, image: Image.Image) -> None:
        self.empty.grid_remove()
        self.canvas.grid()
        self.root.update_idletasks()
        self.editor.set_image(image)
        self.root.title(f"SnipZoom — {image.width} × {image.height}")
        self.canvas.focus_set()
        self._sync_buttons()
        self._set_hint()

    def open_image(self) -> None:
        if self._busy:
            return
        self._dialog = True
        try:
            path = filedialog.askopenfilename(
                parent=self.root,
                title="Open an image",
                filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff"),
                    ("All files", "*.*"),
                ],
            )
        finally:
            self._dialog = False
        if not path:
            return
        try:
            with Image.open(path) as incoming:
                incoming = ImageOps.exif_transpose(incoming) or incoming
                incoming.load()
                frame = incoming.convert("RGBA")
        except Exception as exc:
            messagebox.showerror("SnipZoom", f"Couldn't open that image.\n\n{exc}")
            return
        self._show_image(frame)
        self.flash(f"Opened {Path(path).name}")

    def copy_image(self) -> None:
        if not self.editor.has_image():
            self.flash("Take a snip first")
            return
        try:
            copy_image(self.editor.flattened())
        except Exception as exc:
            messagebox.showerror("SnipZoom", str(exc))
            return
        self.flash("Copied to clipboard")

    def save_image(self) -> None:
        if not self.editor.has_image():
            self.flash("Take a snip first")
            return
        pictures = Path.home() / "Pictures"
        initial = pictures if pictures.is_dir() else Path.home()
        name = datetime.now().strftime("Snip %Y-%m-%d %H%M%S.png")
        self._dialog = True
        try:
            path = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save snip",
                defaultextension=".png",
                initialdir=str(initial),
                initialfile=name,
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("All files", "*.*")],
            )
        finally:
            self._dialog = False
        if not path:
            return
        try:
            if path.lower().endswith((".jpg", ".jpeg")):
                self.editor.flattened().save(path, quality=95)
            else:
                self.editor.flattened_rgba().save(path)
        except Exception as exc:
            messagebox.showerror("SnipZoom", f"Couldn't save the image.\n\n{exc}")
            return
        self.flash(f"Saved {Path(path).name}")

    def toggle_detail(self) -> None:
        self.editor.detail = not self.editor.detail
        self.detail_btn.set_text("Detail" if self.editor.detail else "Pixels")
        self.detail_btn.set_selected(self.editor.detail)
        if self.editor.has_image():
            self.editor.render()
            self._on_view_change(self.editor.zoom, self.editor.base.width, self.editor.base.height)
        self._set_hint()

    def editor_zoom_in(self) -> None:
        self.editor.zoom_in()

    def editor_zoom_out(self) -> None:
        self.editor.zoom_out()

    def editor_undo(self) -> None:
        self.editor.undo()

    def editor_redo(self) -> None:
        self.editor.redo()

    def _on_view_change(self, zoom: float, width: int, height: int) -> None:
        self.percent.configure(text=f"{round(zoom * 100)}%")
        self._size_text = f"{width} × {height}"
        self.root.title(f"SnipZoom — {width} × {height}")
        self._refresh_detail()

    def _on_pointer(self, color: str | None, text: str) -> None:
        self._pointer_text = text
        if color:
            self.chip.configure(bg=color, highlightbackground="#8d8d8d")
        else:
            self.chip.configure(bg=STATUS, highlightbackground=STATUS)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        parts = [part for part in (self._size_text, self._pointer_text) if part]
        self.detail.configure(text="     ".join(parts))

    def _set_hint(self) -> None:
        if not self.editor.has_image():
            self._hint_text = "Ctrl+Shift+S snips from anywhere"
        elif self._tool == "pan" and self.editor.detail:
            self._hint_text = "Scroll to zoom  ·  Detail divides each pixel so curves stay smooth  ·  Drag to pan"
        elif self._tool == "pan":
            self._hint_text = "Scroll to zoom  ·  Pixels shows hard blocks  ·  Drag to pan"
        elif self._tool == "pen":
            self._hint_text = "Drag to draw  ·  Scroll still zooms  ·  Hold Space to pan"
        elif self._tool == "marker":
            self._hint_text = "Drag to highlight  ·  Scroll still zooms  ·  Hold Space to pan"
        else:
            self._hint_text = "Drag to erase marks  ·  Scroll still zooms  ·  Hold Space to pan"
        self.hint.configure(text=self._hint_text)

    def flash(self, text: str) -> None:
        self._flash_gen += 1
        generation = self._flash_gen
        self.hint.configure(text=text)

        def restore(gen=generation) -> None:
            if gen == self._flash_gen and not self._closing:
                self.hint.configure(text=self._hint_text)

        self.root.after(1800, restore)

    def _sync_buttons(self) -> None:
        has = self.editor.has_image()
        for button in (
            self.copy_btn,
            self.save_btn,
            self.zoom_in_btn,
            self.zoom_out_btn,
            self.fit_btn,
            *self.tool_buttons.values(),
        ):
            button.set_enabled(has)
        self.detail_btn.set_enabled(has)
        self.detail_btn.set_selected(has and self.editor.detail)
        self.detail_btn.set_text("Detail" if self.editor.detail else "Pixels")
        self.undo_btn.set_enabled(has and self.editor.can_undo())
        self.redo_btn.set_enabled(has and self.editor.can_redo())
        for key, button in self.tool_buttons.items():
            button.set_selected(has and key == self._tool)
        self.percent.configure(cursor="hand2" if has else "arrow")

    def _on_wheel(self, event) -> str | None:
        if self._busy or self._dialog or self.overlay is not None or not self.editor.has_image():
            return None
        left = self.root.winfo_rootx()
        top = self.root.winfo_rooty()
        right = left + self.root.winfo_width()
        bottom = top + self.root.winfo_height()
        if not (left <= event.x_root < right and top <= event.y_root < bottom):
            return None
        self.editor.wheel(event.x_root, event.y_root, event.delta)
        return "break"

    def _copy_event(self, _event):
        self.copy_image()
        return "break"

    def _save_event(self, _event):
        self.save_image()
        return "break"

    def _undo_event(self, _event):
        self.editor.undo()
        return "break"

    def _redo_event(self, _event):
        self.editor.redo()
        return "break"

    def _space_down(self, _event):
        self.editor.space_down()
        return "break"

    def _space_up(self, _event):
        self.editor.space_up()
        return "break"

    def _on_escape(self, _event=None):
        if self.countdown is not None:
            self.countdown.cancel()
            return "break"
        if self.overlay is not None:
            self.overlay.cancel()
            return "break"
        return None

    def _register_hotkey(self) -> None:
        try:
            self._hwnd = top_level_hwnd(self.root.winfo_id())
            ok = user32.RegisterHotKey(
                self._hwnd,
                HOTKEY_ID,
                MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT,
                VK_S,
            )
            self._hotkey_on = bool(ok)
            if not ok:
                self.flash("Ctrl+Shift+S is already in use. The New snip button still works.")
        except Exception:
            self._hotkey_on = False

    def _poll_hotkey(self) -> None:
        if self._closing:
            return
        msg = MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
            if int(msg.wParam) == HOTKEY_ID:
                self.start_snip()
                break
        self.root.after(80, self._poll_hotkey)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._hotkey_on and self._hwnd:
            try:
                user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
            except Exception:
                pass
        if self.countdown is not None:
            self.countdown.cancelled = True
            try:
                self.countdown.top.destroy()
            except tk.TclError:
                pass
        if self.overlay is not None:
            self.overlay.close()
        self.root.destroy()

    def run(self) -> None:
        self.set_tool("pan")
        self.root.mainloop()


def run() -> None:
    SnipZoomApp().run()
