"""Screen capture, monitor geometry, and clipboard images."""

from __future__ import annotations

import ctypes
import io
import time
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageGrab

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SPI_GETWORKAREA = 0x0030
MONITORINFOF_PRIMARY = 1
CF_DIB = 8
GMEM_MOVEABLE = 0x0002

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_MONITOR_ENUM = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


@dataclass
class Shot:
    image: Image.Image
    origin: tuple[int, int]
    monitors: tuple[tuple[int, int, int, int, bool], ...]


def virtual_screen() -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of every monitor together, in physical pixels."""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        x, y, w, h = virtual_screen()
        return x, y, x + w, y + h
    return rect.left, rect.top, rect.right, rect.bottom


def _enum_monitors() -> list[tuple[int, int, int, int, bool]]:
    found: list[tuple[int, int, int, int, bool]] = []

    def callback(hmon, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            rect = info.rcMonitor
            found.append(
                (
                    int(rect.left),
                    int(rect.top),
                    int(rect.right),
                    int(rect.bottom),
                    bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return 1

    user32.EnumDisplayMonitors(None, None, _MONITOR_ENUM(callback), 0)
    return found


def capture_screen() -> Shot:
    origin_x, origin_y, width, height = virtual_screen()
    image = ImageGrab.grab(
        bbox=(origin_x, origin_y, origin_x + width, origin_y + height),
        all_screens=True,
    )
    if image.mode != "RGB":
        image = image.convert("RGB")
    monitors = []
    for left, top, right, bottom, primary in _enum_monitors():
        box = (
            left - origin_x,
            top - origin_y,
            right - origin_x,
            bottom - origin_y,
            primary,
        )
        monitors.append(_clamp_monitor(box, image.width, image.height))
    return Shot(image, (origin_x, origin_y), tuple(monitors))


def _clamp_monitor(
    box: tuple[int, int, int, int, bool], width: int, height: int
) -> tuple[int, int, int, int, bool]:
    left, top, right, bottom, primary = box
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    right = max(left, min(width, right))
    bottom = max(top, min(height, bottom))
    return left, top, right, bottom, primary


def monitor_at(shot: Shot, image_x: int, image_y: int) -> tuple[int, int, int, int]:
    for left, top, right, bottom, _primary in shot.monitors:
        if left <= image_x < right and top <= image_y < bottom:
            return left, top, right, bottom
    if shot.monitors:
        left, top, right, bottom, _primary = shot.monitors[0]
        return left, top, right, bottom
    return 0, 0, shot.image.width, shot.image.height


def copy_image(image: Image.Image) -> None:
    """Place an image on the clipboard as a device-independent bitmap."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "BMP")
    data = buffer.getvalue()[14:]
    buffer.close()

    if not _open_clipboard():
        raise RuntimeError("The clipboard is busy. Try again in a moment.")

    handle = None
    try:
        if not user32.EmptyClipboard():
            raise RuntimeError("Couldn't empty the clipboard.")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise MemoryError("Couldn't allocate clipboard memory.")
        locked = kernel32.GlobalLock(handle)
        if not locked:
            raise MemoryError("Couldn't lock clipboard memory.")
        ctypes.memmove(locked, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_DIB, handle):
            raise RuntimeError("Couldn't place the image on the clipboard.")
        handle = None
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def _open_clipboard() -> bool:
    for _ in range(10):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.03)
    return False


def top_level_hwnd(tk_id: int) -> int:
    parent = user32.GetParent(tk_id)
    return int(parent or tk_id)
