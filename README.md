# SnipZoom

Snip a region of the screen, then zoom in on it.

Scroll to zoom toward the cursor. **Detail** divides each pixel so curves, such as a watch movement, stay smooth. **Pixels** shows hard blocks instead. Copy, save, pin the window, and mark the snip up with the pen, highlighter, or eraser.

## Windows app

`SnipZoom.exe` is installed for your user. Shortcuts are on the Desktop and in the Start menu. The hotkey is **Ctrl+Shift+S**.

Rebuild and refresh those shortcuts with:

```
py -m pip install -r requirements.txt
py build_exe.py
```

## Run from source

```
py -m pip install -r requirements.txt
py main.py
```
