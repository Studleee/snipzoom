"""Build SnipZoom.exe and add Desktop and Start menu shortcuts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "snipzoom.ico"
MANIFEST = ROOT / "packaging" / "app.manifest"
DIST_EXE = ROOT / "dist" / "SnipZoom.exe"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "SnipZoom"
INSTALL_EXE = INSTALL_DIR / "SnipZoom.exe"


def build_icon() -> None:
    from snipzoom.theme import build_icon as draw_icon

    ICON.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [draw_icon(size) for size in sizes]
    images[0].save(
        ICON,
        format="ICO",
        sizes=[(size, size) for size in sizes],
        append_images=images[1:],
    )


def build_exe() -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onefile",
        "--name",
        "SnipZoom",
        "--icon",
        str(ICON),
        "--manifest",
        str(MANIFEST),
        "main.py",
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    if not DIST_EXE.is_file():
        raise SystemExit(f"Build finished without {DIST_EXE}")


def install_exe() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DIST_EXE, INSTALL_EXE)


def shortcut(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut('{path}')
$link.TargetPath = '{target}'
$link.WorkingDirectory = '{target.parent}'
$link.IconLocation = '{target},0'
$link.Description = 'Snip a region of the screen, then zoom in'
$link.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
    )


def install_shortcuts() -> None:
    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    # Honor a redirected Desktop, such as OneDrive.
    known = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Environment]::GetFolderPath('Desktop')",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if known:
        desktop = Path(known)
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    shortcut(desktop / "SnipZoom.lnk", INSTALL_EXE)
    shortcut(start_menu / "SnipZoom.lnk", INSTALL_EXE)
    print(f"Desktop: {desktop / 'SnipZoom.lnk'}")
    print(f"Start menu: {start_menu / 'SnipZoom.lnk'}")
    print(f"Program: {INSTALL_EXE}")


def main() -> None:
    build_icon()
    build_exe()
    install_exe()
    install_shortcuts()


if __name__ == "__main__":
    main()
