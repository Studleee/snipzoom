"""Full-desktop snip overlay.

The screen is captured first, then shown frozen and dimmed. Dragging punches a
bright hole through that dimming, and a loupe magnifies the pixels under the cursor.
"""

from __future__ import annotations

import time
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

from .screen import Shot, monitor_at
from .theme import ACCENT, FONT, TEXT
from .widgets import FlatButton

LOUPE_SRC = 33
LOUPE_SCALE = 5
LOUPE_DISP = LOUPE_SRC * LOUPE_SCALE
MAX_HOLE_PIXELS = 4_000_000


class SnipOverlay:
    def __init__(self, root: tk.Tk, shot: Shot, on_done, on_cancel):
        self.root = root
        self.shot = shot
        self.image = shot.image
        self.origin = shot.origin
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.start: tuple[int, int] | None = None
        self.rect: tuple[int, int, int, int] | None = None
        self._hole_photo: ImageTk.PhotoImage | None = None
        self._loupe_photo: ImageTk.PhotoImage | None = None
        self._closed = False
        self._last_hole = 0.0
        self._loupe_font = _loupe_font()

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="black", cursor="crosshair")
        self.win.geometry(f"{self.image.width}x{self.image.height}+{self.origin[0]}+{self.origin[1]}")

        self.canvas = tk.Canvas(
            self.win,
            width=self.image.width,
            height=self.image.height,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
            bg="black",
        )
        self.canvas.pack(fill="both", expand=True)

        dimmed = ImageEnhance.Brightness(self.image).enhance(0.55)
        self._bg_photo = ImageTk.PhotoImage(dimmed)
        self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)

        blank = ImageTk.PhotoImage(Image.new("RGB", (1, 1)))
        self._hole_photo = blank
        self.hole_item = self.canvas.create_image(0, 0, anchor="nw", image=blank, state="hidden")
        self.border = self.canvas.create_rectangle(0, 0, 0, 0, outline=ACCENT, width=2, state="hidden")
        self.size_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#101010", outline=ACCENT, state="hidden")
        self.size_font = tkfont.Font(family="Segoe UI", size=10)
        self.size_text = self.canvas.create_text(
            0, 0, text="", fill=TEXT, font=self.size_font, anchor="nw", state="hidden"
        )

        self._loupe_photo = ImageTk.PhotoImage(Image.new("RGB", (1, 1), (0, 0, 0)))
        self.loupe_item = self.canvas.create_image(0, 0, anchor="nw", image=self._loupe_photo, state="hidden")
        self._build_bar()

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.win.bind("<ButtonPress-3>", lambda _e: self.cancel())
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self._confirm_current())
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)

        self.win.update_idletasks()
        self.win.lift()
        self.win.focus_force()
        try:
            self.win.grab_set()
        except tk.TclError:
            pass

    def _build_bar(self) -> None:
        shell = tk.Frame(self.win, bg="#000000")
        shell.place(relx=0.5, y=18, anchor="n")
        self.toolbar = tk.Frame(shell, bg="#2b2b2b")
        self.toolbar.pack(padx=1, pady=1)
        tk.Label(
            self.toolbar,
            text="Drag to select    ·    Shift: square    ·    Esc: cancel",
            bg="#2b2b2b",
            fg=TEXT,
            font=FONT,
            padx=14,
            pady=8,
        ).pack(side="left")
        FlatButton(self.toolbar, "This screen", self._snip_monitor, tooltip="Capture the monitor under the cursor").pack(
            side="left", padx=(0, 6), pady=6
        )
        FlatButton(self.toolbar, "All screens", self._snip_all, tooltip="Capture every monitor").pack(
            side="left", padx=(0, 6), pady=6
        )
        FlatButton(self.toolbar, "Cancel", self.cancel, tooltip="Esc").pack(side="left", padx=(0, 8), pady=6)
        self.toolbar.bind("<Enter>", lambda _e: self._hide_loupe())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def cancel(self) -> None:
        if self._closed:
            return
        callback = self.on_cancel
        self.close()
        callback()

    def _snip_all(self) -> None:
        self._finish((0, 0, self.image.width, self.image.height))

    def _snip_monitor(self) -> None:
        pointer_x = self.win.winfo_pointerx()
        pointer_y = self.win.winfo_pointery()
        ix = pointer_x - self.origin[0]
        iy = pointer_y - self.origin[1]
        self._finish(monitor_at(self.shot, ix, iy))

    def _confirm_current(self) -> None:
        if self.rect and self.rect[2] - self.rect[0] >= 4 and self.rect[3] - self.rect[1] >= 4:
            self._finish(self.rect)

    def _press(self, event) -> None:
        if self._over_toolbar(event):
            return
        x, y = self._image_pos(event)
        self.start = (x, y)
        self.rect = None
        self._hide_selection()

    def _drag(self, event) -> None:
        if self.start is None:
            return
        self.rect = self._selection_rect(event)
        self._draw_selection(self.rect, live_hole=True)
        self._show_loupe(event)

    def _release(self, event) -> None:
        if self.start is None:
            return
        rect = self._selection_rect(event)
        self.start = None
        if rect[2] - rect[0] < 6 or rect[3] - rect[1] < 6:
            self.rect = None
            self._hide_selection()
            return
        self._finish(rect)

    def _motion(self, event) -> None:
        if self._over_toolbar(event):
            self._hide_loupe()
            return
        self._show_loupe(event)

    def _leave(self, _event) -> None:
        if self.start is None:
            self._hide_loupe()

    def _finish(self, box: tuple[int, int, int, int]) -> None:
        if self._closed:
            return
        left, top, right, bottom = box
        left = max(0, min(self.image.width, int(left)))
        top = max(0, min(self.image.height, int(top)))
        right = max(left, min(self.image.width, int(right)))
        bottom = max(top, min(self.image.height, int(bottom)))
        if right - left < 2 or bottom - top < 2:
            return
        crop = self.image.crop((left, top, right, bottom))
        callback = self.on_done
        self.close()
        callback(crop)

    def _image_pos(self, event) -> tuple[int, int]:
        x = int(event.x_root - self.origin[0])
        y = int(event.y_root - self.origin[1])
        return x, y

    def _selection_rect(self, event) -> tuple[int, int, int, int]:
        assert self.start is not None
        x, y = self._image_pos(event)
        x0, y0 = self.start
        if event.state & 0x0001:
            dx = x - x0
            dy = y - y0
            side = max(abs(dx), abs(dy))
            x = x0 + (side if dx >= 0 else -side)
            y = y0 + (side if dy >= 0 else -side)
        left, right = sorted((int(x0), int(x)))
        top, bottom = sorted((int(y0), int(y)))
        left = max(0, min(self.image.width, left))
        right = max(0, min(self.image.width, right))
        top = max(0, min(self.image.height, top))
        bottom = max(0, min(self.image.height, bottom))
        return left, top, right, bottom

    def _draw_selection(self, rect: tuple[int, int, int, int], live_hole: bool) -> None:
        left, top, right, bottom = rect
        self.canvas.coords(self.border, left, top, right, bottom)
        self.canvas.itemconfigure(self.border, state="normal")
        self._place_size(rect)
        if not live_hole:
            return
        area = (right - left) * (bottom - top)
        if area <= 0 or area > MAX_HOLE_PIXELS or right <= left or bottom <= top:
            self.canvas.itemconfigure(self.hole_item, state="hidden")
        else:
            now = time.perf_counter()
            if now - self._last_hole >= 0.045:
                self._last_hole = now
                crop = self.image.crop(rect)
                self._hole_photo = ImageTk.PhotoImage(crop)
                self.canvas.itemconfigure(self.hole_item, image=self._hole_photo, state="normal")
                self.canvas.coords(self.hole_item, left, top)
        self._raise_chrome()

    def _place_size(self, rect: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if width < 1 or height < 1:
            self.canvas.itemconfigure(self.size_bg, state="hidden")
            self.canvas.itemconfigure(self.size_text, state="hidden")
            return
        label = f"{width} × {height}"
        text_w = self.size_font.measure(label)
        text_h = self.size_font.metrics("linespace")
        pad_x, pad_y = 8, 4
        box_w = text_w + pad_x * 2
        box_h = text_h + pad_y * 2
        if height > box_h + 10 and width > box_w + 10:
            x = left + 6
            y = top + 6
        else:
            x = left
            y = bottom + 8
        if x + box_w > self.image.width:
            x = max(0, self.image.width - box_w - 2)
        if y + box_h > self.image.height:
            y = max(0, top - box_h - 8)
        self.canvas.coords(self.size_bg, x, y, x + box_w, y + box_h)
        self.canvas.coords(self.size_text, x + pad_x, y + pad_y)
        self.canvas.itemconfigure(self.size_text, text=label, state="normal")
        self.canvas.itemconfigure(self.size_bg, state="normal")
        self._raise_chrome()

    def _raise_chrome(self) -> None:
        self.canvas.tag_raise(self.hole_item)
        self.canvas.tag_raise(self.border)
        self.canvas.tag_raise(self.size_bg)
        self.canvas.tag_raise(self.size_text)
        self.canvas.tag_raise(self.loupe_item)

    def _hide_selection(self) -> None:
        for item in (self.hole_item, self.border, self.size_bg, self.size_text):
            self.canvas.itemconfigure(item, state="hidden")

    def _hide_loupe(self) -> None:
        self.canvas.itemconfigure(self.loupe_item, state="hidden")

    def _show_loupe(self, event) -> None:
        if self._closed:
            return
        ix = int(event.x_root - self.origin[0])
        iy = int(event.y_root - self.origin[1])
        ix = max(0, min(self.image.width - 1, ix))
        iy = max(0, min(self.image.height - 1, iy))
        loupe = self._render_loupe(ix, iy)
        self._loupe_photo = ImageTk.PhotoImage(loupe)
        self.canvas.itemconfigure(self.loupe_item, image=self._loupe_photo, state="normal")
        cursor_x = int(event.x_root - self.origin[0])
        cursor_y = int(event.y_root - self.origin[1])
        x = cursor_x + 28
        y = cursor_y + 28
        if x + loupe.width > self.image.width - 4:
            x = cursor_x - 28 - loupe.width
        if y + loupe.height > self.image.height - 4:
            y = cursor_y - 28 - loupe.height
        x = max(0, min(self.image.width - loupe.width, x))
        y = max(0, min(self.image.height - loupe.height, y))
        self.canvas.coords(self.loupe_item, x, y)
        self.canvas.tag_raise(self.loupe_item)

    def _render_loupe(self, ix: int, iy: int) -> Image.Image:
        half = LOUPE_SRC // 2
        tile = Image.new("RGB", (LOUPE_SRC, LOUPE_SRC), (24, 24, 24))
        left = ix - half
        top = iy - half
        src_l = max(0, left)
        src_t = max(0, top)
        src_r = min(self.image.width, left + LOUPE_SRC)
        src_b = min(self.image.height, top + LOUPE_SRC)
        if src_r > src_l and src_b > src_t:
            tile.paste(self.image.crop((src_l, src_t, src_r, src_b)), (src_l - left, src_t - top))
        big = tile.resize((LOUPE_DISP, LOUPE_DISP), Image.Resampling.NEAREST)
        draw = ImageDraw.Draw(big)
        x0 = half * LOUPE_SCALE
        y0 = half * LOUPE_SCALE
        x1 = x0 + LOUPE_SCALE - 1
        y1 = y0 + LOUPE_SCALE - 1
        mid_x = x0 + LOUPE_SCALE // 2
        mid_y = y0 + LOUPE_SCALE // 2
        color = (62, 198, 255)
        draw.line([(mid_x, 0), (mid_x, y0 - 1)], fill=color)
        draw.line([(mid_x, y1 + 1), (mid_x, LOUPE_DISP)], fill=color)
        draw.line([(0, mid_y), (x0 - 1, mid_y)], fill=color)
        draw.line([(x1 + 1, mid_y), (LOUPE_DISP, mid_y)], fill=color)
        draw.rectangle([x0, y0, x1, y1], outline=color)

        red, green, blue = self.image.getpixel((ix, iy))
        if self.rect and self.start is not None:
            caption = f"5×    {self.rect[2] - self.rect[0]} × {self.rect[3] - self.rect[1]}"
        else:
            caption = f"5×    #{red:02X}{green:02X}{blue:02X}"
        border = 2
        caption_h = 26
        frame = Image.new("RGB", (big.width + border * 2, big.height + caption_h + border * 2), color)
        frame.paste(big, (border, border))
        draw = ImageDraw.Draw(frame)
        draw.rectangle(
            [border, border + big.height, frame.width - border - 1, frame.height - border - 1],
            fill=(27, 27, 27),
        )
        draw.text((border + 8, border + big.height + 4), caption, fill=(230, 230, 230), font=self._loupe_font)
        return frame

    def _over_toolbar(self, event) -> bool:
        widget = self.win.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self.toolbar:
                return True
            widget = getattr(widget, "master", None)
        return False


def _loupe_font():
    for path in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            return ImageFont.truetype(path, 13)
        except OSError:
            continue
    return ImageFont.load_default()
