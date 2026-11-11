# =============================================================================
# FILE: app/no_console.py  -  NO FLASHING BLACK WINDOWS (Windows only)
# -----------------------------------------------------------------------------
# LanWatch runs command-line tools in the background:
#   netsh  (Wi-Fi name and signal, every dashboard refresh)
#   nmap   (scans, through the python-nmap library)
#   route  (finding the router after full scans)
#
# The installed LanWatch.exe has no console window of its own. When a program
# like that starts a command-line tool, Windows opens a NEW black console
# window for the tool, which disappears again when it finishes: a window
# flashing up every few seconds.
#
# hide_console_windows() makes every tool LanWatch starts run with the
# CREATE_NO_WINDOW flag, so no window ever appears. It changes Python's
# subprocess.Popen once, which covers LanWatch's own code AND libraries such
# as python-nmap that start nmap themselves. Called from app/__init__.py,
# so it is active before anything else runs.
# =============================================================================

import subprocess
import sys


def hide_console_windows():
    """Start every child program without a console window (no-op outside Windows)."""
    if sys.platform != "win32" or getattr(subprocess.Popen, "_lanwatch_no_console", False):
        return

    original_init = subprocess.Popen.__init__

    def popen_without_window(self, *args, **kwargs):
        # only when the caller didn't choose window settings itself
        if not kwargs.get("creationflags") and len(args) < 12:   # creationflags is Popen's 12th argument
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = popen_without_window
    subprocess.Popen._lanwatch_no_console = True
