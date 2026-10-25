"""SnipZoom — region snips with a zoomable viewer."""

import ctypes

# Must run before Tk is created so captures and windows share physical pixels.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SnipZoom.Tool")
except Exception:
    pass
