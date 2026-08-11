"""Cross-platform PyInstaller build driver.

PyInstaller doesn't cross-compile — this script just picks the right flags
(icon, --add-data separator, onefile vs onedir) for whichever OS it's run on.
The GitHub Actions matrix runs this natively on macOS, Windows, and Linux.

Usage:
    python build.py                 # OS-appropriate default mode
    PYI_MODE=onefile python build.py   # force onefile
    PYI_MODE=onedir python build.py    # force onedir
"""
import os
import subprocess
import sys

APP_NAME = "Snippet Manager"
ENTRY = "snippet_manager_app.py"


def main():
    is_mac = sys.platform == "darwin"
    is_win = sys.platform == "win32"

    # onedir starts faster and is the natural fit for a macOS .app bundle.
    # onefile is the simplest single-artifact distribution on Windows/Linux.
    # Override with PYI_MODE if you want to benchmark the other mode on a
    # given OS.
    default_mode = "onedir" if is_mac else "onefile"
    mode = os.environ.get("PYI_MODE", default_mode)

    add_data_sep = ";" if is_win else ":"

    icon = None
    if is_mac and os.path.exists("assets/icon.icns"):
        icon = "assets/icon.icns"
    elif is_win and os.path.exists("assets/icon.ico"):
        icon = "assets/icon.ico"
    # Linux .desktop files reference an icon separately — PyInstaller doesn't
    # embed one into the ELF binary, so nothing to pass here.

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--windowed",
        f"--{mode}",
        "--name", APP_NAME,
        "--add-data", f"ui{add_data_sep}ui",
        "--noconfirm",
    ]
    if icon:
        cmd += ["--icon", icon]
    cmd.append(ENTRY)

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
