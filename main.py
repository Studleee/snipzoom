"""Start SnipZoom.

Double-click SnipZoom.bat, or run:  py main.py
"""

from __future__ import annotations

import traceback

from snipzoom.app import run


def main() -> None:
    try:
        run()
    except Exception:
        text = traceback.format_exc()
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text[-1200:], "SnipZoom", 0x10)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
