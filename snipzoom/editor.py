"""Zoomable screenshot viewer.

Scroll zooms toward the cursor. Detail mode subdivides each captured pixel from
its neighbors, which keeps curves readable on things like a watch movement.
Pixel mode instead repeats each pixel as a solid block.
"""

from __future__ import annotations

import math
import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from .theme import CANVAS_RGB, MARKER_ALPHA, hex_rgb, ui_scale

MIN_ZOOM = 0.02
MAX_ZOOM = 32.0
MAX_UNDO = 8
GRID_ZOOM = 12.0
# Whole-pixel steps. 150% would split pixels into uneven sizes and look soft.
CRISP_ZOOMS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 32)


def next_crisp_zoom(zoom: float) -> float:
    for step in CRISP_ZOOMS:
        if step > zoom + 0.05:
            return float(step)
    return float(CRISP_ZOOMS[-1])


def prev_crisp_zoom(zoom: float) -> float:
    previous = 1.0
    for step in CRISP_ZOOMS:
        if step < zoom - 0.05:
            previous = float(step)
        else:
            break
    return previous


def clamp_offset(
    ox: float,
    oy: float,
    zoom: float,
    view_w: int,
    view_h: int,
    img_w: int,
    img_h: int,
) -> tuple[float, float]:
    src_w = view_w / zoom
    src_h = view_h / zoom
    if img_w <= src_w:
        ox = (img_w - src_w) / 2
    else:
        ox = min(max(ox, 0.0), img_w - src_w)
    if img_h <= src_h:
        oy = (img_h - src_h) / 2
    else:
        oy = min(max(oy, 0.0), img_h - src_h)
    return ox, oy


class Editor:
    def __init__(self, canvas: tk.Canvas, on_change, on_pointer, on_history):
        self.canvas = canvas
        self.on_change = on_change
        self.on_pointer = on_pointer
        self.on_history = on_history
        self.base: Image.Image | None = None
        self.annotation: Image.Image | None = None
        self._undo_stack: list[Image.Image] = []
        self._redo_stack: list[Image.Image] = []
        self.zoom = 1.0
        self.ox = 0.0
        self.oy = 0.0
        self.fitted = True
        self.detail = True
        self.tool = "pan"
        self.pen_color = "#e74856"
        self.marker_color = "#fff200"
        self.space = False
        self.drawing = False
        self._pan_ready = False
        self._pan_active = False
        self._press_xy = (0, 0)
        self._press_origin = (0.0, 0.0)
        self.points: list[tuple[float, float]] = []
        self._stroke_base: Image.Image | None = None
        self._stroke_width = 1
        self._stroke_color = (231, 72, 86, 255)
        self._photo: tk.PhotoImage | None = None
        self._image_item = None
        self.view_image: Image.Image | None = None
        self._canvas_size = (0, 0)
        self._wheel_bucket = 0
        self.ui_scale = ui_scale(canvas)
        self._bind()

    def _bind(self) -> None:
        canvas = self.canvas
        canvas.bind("<ButtonPress-1>", self.on_left_press)
        canvas.bind("<B1-Motion>", self.on_left_motion)
        canvas.bind("<ButtonRelease-1>", self.on_left_release)
        canvas.bind("<ButtonPress-2>", self.on_middle_press)
        canvas.bind("<B2-Motion>", self.on_pan_motion)
        canvas.bind("<ButtonRelease-2>", self.on_pan_release)
        canvas.bind("<Double-Button-1>", self.on_double)
        canvas.bind("<Motion>", self.on_hover)
        canvas.bind("<Leave>", self.on_leave)
        canvas.bind("<Configure>", self.on_configure)

    def set_image(self, image: Image.Image) -> None:
        self.base = image.convert("RGBA")
        self.annotation = Image.new("RGBA", self.base.size, (0, 0, 0, 0))
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.drawing = False
        self._pan_ready = False
        self._pan_active = False
        self.fitted = True
        self.zoom = 1.0
        self.ox = 0.0
        self.oy = 0.0
        self._notify_history()
        self.canvas.update_idletasks()
        if self.canvas.winfo_width() > 20 and self.canvas.winfo_height() > 20:
            self.fit()
        else:
            self.canvas.after_idle(self.fit)
        self._update_cursor()

    def has_image(self) -> bool:
        return self.base is not None

    def set_tool(self, tool: str) -> None:
        if self.drawing:
            self._end_draw()
        self.tool = tool
        self._update_cursor()

    def set_pen_color(self, color: str) -> None:
        self.pen_color = color

    def set_marker_color(self, color: str) -> None:
        self.marker_color = color

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack) and not self.drawing

    def undo(self) -> None:
        if self.base is None:
            return
        if self.drawing:
            self.drawing = False
            self._pan_ready = False
            if self._undo_stack:
                self.annotation = self._undo_stack.pop()
            self._stroke_base = None
            self.points.clear()
            self.render()
            self._notify_history()
            return
        if not self._undo_stack:
            return
        self._redo_stack.append(self.annotation)
        self.annotation = self._undo_stack.pop()
        self.render()
        self._notify_history()

    def redo(self) -> None:
        if self.base is None or self.drawing or not self._redo_stack:
            return
        self._undo_stack.append(self.annotation)
        self.annotation = self._redo_stack.pop()
        self.render()
        self._notify_history()

    def flattened(self, *, background=(255, 255, 255, 255)) -> Image.Image:
        if self.base is None or self.annotation is None:
            raise RuntimeError("There is no snip to copy.")
        backdrop = Image.new("RGBA", self.base.size, background)
        return Image.alpha_composite(Image.alpha_composite(backdrop, self.base), self.annotation).convert("RGB")

    def flattened_rgba(self) -> Image.Image:
        if self.base is None or self.annotation is None:
            raise RuntimeError("There is no snip to save.")
        return Image.alpha_composite(self.base, self.annotation)

    def fit(self) -> None:
        if self.base is None:
            return
        view_w = self.canvas.winfo_width()
        view_h = self.canvas.winfo_height()
        if view_w < 20 or view_h < 20:
            return
        pad = 32
        zoom = min(
            max(1, view_w - pad) / self.base.width,
            max(1, view_h - pad) / self.base.height,
            1.0,
        )
        self.zoom = max(MIN_ZOOM, zoom)
        self.fitted = True
        self.ox, self.oy = clamp_offset(
            0, 0, self.zoom, view_w, view_h, self.base.width, self.base.height
        )
        self.render()
        self._notify()

    def actual(self) -> None:
        if self.base is None:
            return
        self.fitted = False
        self._set_zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, 1.0)

    def zoom_in(self) -> None:
        if self.base is None:
            return
        self.fitted = False
        self._zoom_in_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)

    def zoom_out(self) -> None:
        if self.base is None:
            return
        self.fitted = False
        self._zoom_out_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)

    def wheel(self, x_root: int, y_root: int, delta: int) -> None:
        if self.base is None or delta == 0:
            return
        self._wheel_bucket += delta
        steps = int(self._wheel_bucket / 120)
        if steps == 0:
            return
        self._wheel_bucket -= steps * 120
        sx = x_root - self.canvas.winfo_rootx()
        sy = y_root - self.canvas.winfo_rooty()
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if not (0 <= sx < width and 0 <= sy < height):
            sx = width / 2
            sy = height / 2
        self.fitted = False
        if steps > 0:
            for _ in range(steps):
                self._zoom_in_at(sx, sy)
        else:
            for _ in range(-steps):
                self._zoom_out_at(sx, sy)

    def space_down(self) -> None:
        if self.space:
            return
        self.space = True
        self._update_cursor()

    def space_up(self) -> None:
        self.space = False
        self._update_cursor()

    def on_configure(self, event) -> None:
        if event.widget is not self.canvas or self.base is None:
            return
        size = (event.width, event.height)
        if size == self._canvas_size or event.width < 20 or event.height < 20:
            return
        self._canvas_size = size
        if self.fitted:
            self.fit()
        else:
            self.clamp()
            self.render()

    def on_left_press(self, event) -> None:
        if self.base is None:
            return
        self.canvas.focus_set()
        if self.tool == "pan" or self.space:
            self._begin_pan(event)
        else:
            self._begin_draw(event)

    def on_left_motion(self, event) -> None:
        self._update_pointer(event)
        if self.drawing:
            self._continue_draw(event)
        elif self._pan_ready:
            self._continue_pan(event)

    def on_left_release(self, _event) -> None:
        if self.drawing:
            self._end_draw()
        self._pan_ready = False
        self._pan_active = False
        self._update_cursor()

    def on_middle_press(self, event) -> None:
        if self.base is None:
            return
        self._begin_pan(event)

    def on_pan_motion(self, event) -> None:
        self._update_pointer(event)
        if self._pan_ready:
            self._continue_pan(event)

    def on_pan_release(self, _event) -> None:
        self._pan_ready = False
        self._pan_active = False
        self._update_cursor()

    def on_double(self, event) -> None:
        if self.base is None or self.tool != "pan" or self.space:
            return "break"
        self._pan_ready = False
        self._pan_active = False
        if 0.98 <= self.zoom <= 1.02:
            self.fit()
        else:
            self._set_zoom_at(event.x, event.y, 1.0)
        return "break"

    def on_hover(self, event) -> None:
        self._update_pointer(event)

    def on_leave(self, _event) -> None:
        if not self.drawing and not self._pan_active:
            self._emit_pointer(None, "")

    def clamp(self) -> None:
        if self.base is None:
            return
        view_w = max(1, self.canvas.winfo_width())
        view_h = max(1, self.canvas.winfo_height())
        self.ox, self.oy = clamp_offset(
            self.ox, self.oy, self.zoom, view_w, view_h, self.base.width, self.base.height
        )
        if self.zoom < 1 or self.detail:
            return
        # Whole-pixel zoom stays sharp only when the view is aligned to pixels.
        self.zoom = float(max(1, round(self.zoom)))
        src_w = view_w / self.zoom
        src_h = view_h / self.zoom
        if self.base.width > src_w:
            self.ox = float(round(self.ox))
        if self.base.height > src_h:
            self.oy = float(round(self.oy))
        self.ox, self.oy = clamp_offset(
            self.ox, self.oy, self.zoom, view_w, view_h, self.base.width, self.base.height
        )

    def render(self) -> None:
        if self.base is None or self.annotation is None:
            return
        view_w = max(1, self.canvas.winfo_width())
        view_h = max(1, self.canvas.winfo_height())
        if view_w < 2 or view_h < 2:
            return
        zoom = self.zoom
        src_w = view_w / zoom
        src_h = view_h / zoom
        left = max(0.0, self.ox)
        top = max(0.0, self.oy)
        right = min(float(self.base.width), self.ox + src_w)
        bottom = min(float(self.base.height), self.oy + src_h)

        view = Image.new("RGB", (view_w, view_h), CANVAS_RGB)
        if right > left and bottom > top:
            # Extra source pixels give the subdivision filter neighbors to blend from.
            pad = 3 if self.detail and zoom > 1 else 0
            crop_l = max(0, math.floor(left) - pad)
            crop_t = max(0, math.floor(top) - pad)
            crop_r = min(self.base.width, max(crop_l + 1, math.ceil(right) + pad))
            crop_b = min(self.base.height, max(crop_t + 1, math.ceil(bottom) + pad))
            base_crop = self.base.crop((crop_l, crop_t, crop_r, crop_b))
            ann_crop = self.annotation.crop((crop_l, crop_t, crop_r, crop_b))
            merged = Image.alpha_composite(base_crop, ann_crop)
            crisp = (not self.detail) and zoom >= 1 and abs(zoom - round(zoom)) < 0.02
            if self.detail and zoom > 1:
                scale = zoom
                dest_w = max(1, round((crop_r - crop_l) * zoom))
                dest_h = max(1, round((crop_b - crop_t) * zoom))
                flat = Image.alpha_composite(
                    Image.new("RGBA", merged.size, CANVAS_RGB + (255,)),
                    merged,
                ).convert("RGB")
                resized = flat.resize((dest_w, dest_h), Image.Resampling.LANCZOS)
                resized = _sharpen_subdivided(resized, zoom)
            elif crisp:
                scale = max(1, int(round(zoom)))
                dest_w = max(1, (crop_r - crop_l) * scale)
                dest_h = max(1, (crop_b - crop_t) * scale)
                resized = merged.resize((dest_w, dest_h), Image.Resampling.NEAREST)
            else:
                scale = zoom
                dest_w = max(1, round((crop_r - crop_l) * zoom))
                dest_h = max(1, round((crop_b - crop_t) * zoom))
                resized = merged.resize((dest_w, dest_h), Image.Resampling.BOX)
            paste_x = int(round((crop_l - self.ox) * scale))
            paste_y = int(round((crop_t - self.oy) * scale))
            if resized.mode == "RGBA":
                view.paste(resized, (paste_x, paste_y), resized)
            else:
                view.paste(resized, (paste_x, paste_y))
            self._draw_bounds(view, view_w, view_h, zoom)
            if zoom >= GRID_ZOOM and not self.detail:
                self._draw_grid(view, view_w, view_h, zoom)

        self.view_image = view
        self._photo = ImageTk.PhotoImage(view)
        if self._image_item is None:
            self._image_item = self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self.canvas.itemconfig(self._image_item, image=self._photo)
            self.canvas.coords(self._image_item, 0, 0)

    def _draw_bounds(self, view: Image.Image, view_w: int, view_h: int, zoom: float) -> None:
        if self.base is None:
            return
        x0 = round((0 - self.ox) * zoom)
        y0 = round((0 - self.oy) * zoom)
        x1 = round((self.base.width - self.ox) * zoom) - 1
        y1 = round((self.base.height - self.oy) * zoom) - 1
        if x0 <= 0 and y0 <= 0 and x1 >= view_w - 1 and y1 >= view_h - 1:
            return
        ImageDraw.Draw(view).rectangle([x0, y0, x1, y1], outline=(70, 70, 70))

    def _draw_grid(self, view: Image.Image, view_w: int, view_h: int, zoom: float) -> None:
        if self.base is None:
            return
        draw = ImageDraw.Draw(view)
        start_x = math.ceil(self.ox)
        end_x = math.floor(self.ox + view_w / zoom)
        start_y = math.ceil(self.oy)
        end_y = math.floor(self.oy + view_h / zoom)
        start_x = max(start_x, 0)
        start_y = max(start_y, 0)
        end_x = min(end_x, self.base.width)
        end_y = min(end_y, self.base.height)
        for i in range(start_x, end_x + 1):
            x = round((i - self.ox) * zoom)
            if 0 <= x < view_w:
                draw.line([(x, 0), (x, view_h)], fill=(20, 20, 20))
                if x + 1 < view_w:
                    draw.line([(x + 1, 0), (x + 1, view_h)], fill=(235, 235, 235))
        for i in range(start_y, end_y + 1):
            y = round((i - self.oy) * zoom)
            if 0 <= y < view_h:
                draw.line([(0, y), (view_w, y)], fill=(20, 20, 20))
                if y + 1 < view_h:
                    draw.line([(0, y + 1), (view_w, y + 1)], fill=(235, 235, 235))

    def _begin_pan(self, event) -> None:
        self.drawing = False
        self._pan_ready = True
        self._pan_active = False
        self._press_xy = (event.x, event.y)
        self._press_origin = (self.ox, self.oy)

    def _continue_pan(self, event) -> None:
        if not self._pan_active:
            if abs(event.x - self._press_xy[0]) + abs(event.y - self._press_xy[1]) < 3:
                return
            self._pan_active = True
            self.fitted = False
            self._update_cursor()
        self.ox = self._press_origin[0] - (event.x - self._press_xy[0]) / self.zoom
        self.oy = self._press_origin[1] - (event.y - self._press_xy[1]) / self.zoom
        self.clamp()
        self.render()

    def _begin_draw(self, event) -> None:
        if self.annotation is None:
            return
        point = self._image_point(event.x, event.y)
        self._pan_ready = False
        self.drawing = True
        self.points = [point]
        self._stroke_width = self._nib_width()
        self._stroke_color = self._active_color()
        self._undo_stack.append(self.annotation)
        if len(self._undo_stack) > MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._stroke_base = self.annotation
        self.annotation = self._apply_stroke(
            self._stroke_base, self.points, self.tool, self._stroke_color, self._stroke_width
        )
        self._notify_history()
        self.render()

    def _continue_draw(self, event) -> None:
        if self._stroke_base is None:
            return
        point = self._image_point(event.x, event.y)
        last = self.points[-1]
        if abs(point[0] - last[0]) < 0.35 and abs(point[1] - last[1]) < 0.35:
            return
        self.points.append(point)
        self.annotation = self._apply_stroke(
            self._stroke_base, self.points, self.tool, self._stroke_color, self._stroke_width
        )
        self.render()

    def _end_draw(self) -> None:
        self.drawing = False
        self._stroke_base = None
        self.points.clear()

    def _image_point(self, sx: float, sy: float) -> tuple[float, float]:
        assert self.base is not None
        ix = self.ox + sx / self.zoom
        iy = self.oy + sy / self.zoom
        ix = min(max(ix, 0.0), self.base.width - 1)
        iy = min(max(iy, 0.0), self.base.height - 1)
        return ix, iy

    def _nib_width(self) -> int:
        screen_px = {"pen": 4, "marker": 18, "eraser": 22}.get(self.tool, 4)
        screen_px = max(1, round(screen_px * self.ui_scale))
        return max(1, round(screen_px / self.zoom))

    def _active_color(self) -> tuple[int, int, int, int]:
        if self.tool == "marker":
            red, green, blue = hex_rgb(self.marker_color)
            return red, green, blue, MARKER_ALPHA
        red, green, blue = hex_rgb(self.pen_color)
        return red, green, blue, 255

    def _apply_stroke(self, base, points, tool, color, width) -> Image.Image:
        if not points:
            return base
        left, top, right, bottom = _stroke_bbox(points, width, base.size)
        if right <= left or bottom <= top:
            return base
        shifted = [(x - left, y - top) for x, y in points]
        region = base.crop((left, top, right, bottom))
        painted = _paint_region(region, shifted, tool, color, width)
        output = base.copy()
        output.paste(painted, (left, top))
        return output

    def _zoom_in_at(self, sx: float, sy: float) -> None:
        if self.detail:
            self._set_zoom_at(sx, sy, min(MAX_ZOOM, self.zoom * 1.12))
            return
        if self.zoom < 0.999:
            new_zoom = min(1.0, self.zoom * 1.15)
            self._set_zoom_at(sx, sy, 1.0 if new_zoom >= 0.999 else new_zoom)
            return
        self._set_zoom_at(sx, sy, next_crisp_zoom(self.zoom))

    def _zoom_out_at(self, sx: float, sy: float) -> None:
        if self.detail:
            self._set_zoom_at(sx, sy, max(MIN_ZOOM, self.zoom / 1.12))
            return
        if self.zoom > 1.01:
            self._set_zoom_at(sx, sy, prev_crisp_zoom(self.zoom))
            return
        self._set_zoom_at(sx, sy, max(MIN_ZOOM, self.zoom / 1.15))

    def _set_zoom_at(self, sx: float, sy: float, new_zoom: float) -> None:
        if self.base is None:
            return
        new_zoom = min(MAX_ZOOM, max(MIN_ZOOM, new_zoom))
        ix = self.ox + sx / max(self.zoom, 1e-9)
        iy = self.oy + sy / max(self.zoom, 1e-9)
        self.zoom = new_zoom
        self.ox = ix - sx / self.zoom
        self.oy = iy - sy / self.zoom
        self.fitted = False
        self.clamp()
        self.render()
        self._notify()

    def _update_pointer(self, event) -> None:
        if self.base is None or self.annotation is None:
            self._emit_pointer(None, "")
            return
        ix = int(self.ox + event.x / self.zoom)
        iy = int(self.oy + event.y / self.zoom)
        if not (0 <= ix < self.base.width and 0 <= iy < self.base.height):
            self._emit_pointer(None, "")
            return
        color = _seen_color(self.base, self.annotation, ix, iy)
        text = f"x {ix}, y {iy}    #{color[0]:02X}{color[1]:02X}{color[2]:02X}"
        self._emit_pointer(f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}", text)

    def _emit_pointer(self, color, text) -> None:
        if self.on_pointer is not None:
            self.on_pointer(color, text)

    def _notify(self) -> None:
        if self.on_change is not None and self.base is not None:
            self.on_change(self.zoom, self.base.width, self.base.height)

    def _notify_history(self) -> None:
        if self.on_history is not None:
            self.on_history()

    def _update_cursor(self) -> None:
        if self.base is None:
            cursor = "arrow"
        elif self.space or self._pan_active:
            cursor = "fleur"
        elif self.tool == "pan":
            cursor = "hand2"
        elif self.tool == "eraser":
            cursor = "circle"
        else:
            cursor = "pencil"
        self.canvas.configure(cursor=cursor)


def _sharpen_subdivided(image: Image.Image, zoom: float) -> Image.Image:
    """Pull edges back after the pixel is divided, without crunching flat metal."""
    radius = min(2.0, 0.55 + 0.12 * zoom)
    return image.filter(ImageFilter.UnsharpMask(radius=radius, percent=80, threshold=2))


def _stroke_bbox(points, width: int, size: tuple[int, int]) -> tuple[int, int, int, int]:
    pad = width + 2
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left = max(0, int(min(xs) - pad))
    top = max(0, int(min(ys) - pad))
    right = min(size[0], int(max(xs) + pad + 1))
    bottom = min(size[1], int(max(ys) + pad + 1))
    return left, top, right, bottom


def _draw_nib(draw: ImageDraw.ImageDraw, points, fill, width: int) -> None:
    radius = width / 2
    if len(points) == 1:
        x, y = points[0]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)
        return
    try:
        if len(points) >= 3:
            draw.line(points, fill=fill, width=width, joint="curve")
        else:
            draw.line(points, fill=fill, width=width)
    except TypeError:
        draw.line(points, fill=fill, width=width)
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _paint_region(region: Image.Image, points, tool: str, color, width: int) -> Image.Image:
    if tool == "eraser":
        mask = Image.new("L", region.size, 0)
        _draw_nib(ImageDraw.Draw(mask), points, 255, width)
        output = region.copy()
        output.paste(Image.new("RGBA", region.size, (0, 0, 0, 0)), mask=mask)
        return output
    if tool == "marker":
        layer = Image.new("RGBA", region.size, (0, 0, 0, 0))
        _draw_nib(ImageDraw.Draw(layer), points, (color[0], color[1], color[2], 255), width)
        red, green, blue, alpha = layer.split()
        alpha = alpha.point([0] + [color[3]] * 255)
        layer = Image.merge("RGBA", (red, green, blue, alpha))
        return Image.alpha_composite(region, layer)
    output = region.copy()
    _draw_nib(ImageDraw.Draw(output), points, color, width)
    return output


def _seen_color(base: Image.Image, annotation: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    br, bg, bb, ba = base.getpixel((x, y))
    ar, ag, ab, aa = annotation.getpixel((x, y))
    backdrop = CANVAS_RGB
    base_alpha = ba / 255
    red = br * base_alpha + backdrop[0] * (1 - base_alpha)
    green = bg * base_alpha + backdrop[1] * (1 - base_alpha)
    blue = bb * base_alpha + backdrop[2] * (1 - base_alpha)
    mark = aa / 255
    return (
        round(ar * mark + red * (1 - mark)),
        round(ag * mark + green * (1 - mark)),
        round(ab * mark + blue * (1 - mark)),
    )

