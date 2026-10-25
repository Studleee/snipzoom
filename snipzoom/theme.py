"""Colors, fonts, and small window helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image, ImageDraw

BG = "#1b1b1b"
BAR = "#2c2c2c"
BAR_EDGE = "#121212"
STATUS = "#202020"
CANVAS = "#101010"
CANVAS_RGB = (16, 16, 16)
TEXT = "#f4f4f4"
MUTED = "#b7b7b7"
ACCENT = "#3ec6ff"
ACCENT_BUTTON = "#0e8ec4"
HAIRLINE = "#3f3f3f"

FONT = ("Segoe UI", 10)
FONT_SM = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI", 18)
FONT_BODY = ("Segoe UI", 11)

PEN_COLORS = (
    "#e74856",
    "#ffb900",
    "#10893e",
    "#0078d4",
    "#b146c2",
    "#ffffff",
    "#1a1a1a",
)
MARKER_COLORS = ("#fff200", "#7cfc00", "#00e5ff", "#ff6ad5", "#ff9f1a")
MARKER_ALPHA = 118


def ui_scale(widget) -> float:
    """Return 1.0 at 96 DPI, 1.25 at 120 DPI, and so on."""
    try:
        scaling = float(widget.tk.call("tk", "scaling"))
    except Exception:
        return 1.0
    return max(1.0, scaling / (96 / 72))


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def enable_dark_titlebar(widget) -> None:
    try:
        hwnd = ctypes.windll.user32.GetParent(widget.winfo_id()) or widget.winfo_id()
        value = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        for attr in (20, 19):
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def build_icon(size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, size // 14)
    draw.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=max(4, size // 5),
        fill=(14, 142, 196, 255),
    )
    pad = int(size * 0.30)
    width = max(2, size // 14)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=max(2, size // 18),
        outline=(255, 255, 255, 255),
        width=width,
    )
    radius = size * 0.15
    cx, cy = size * 0.64, size * 0.64
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=(255, 255, 255, 255),
        width=width,
    )
    draw.line(
        [cx + radius * 0.65, cy + radius * 0.65, cx + radius * 1.65, cy + radius * 1.65],
        fill=(255, 255, 255, 255),
        width=width + 1,
    )
    return image
