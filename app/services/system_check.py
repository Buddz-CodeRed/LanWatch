# =============================================================================
# FILE: app/services/system_check.py  -  "IS EVERYTHING LANWATCH NEEDS HERE?"
# -----------------------------------------------------------------------------
# LanWatch relies on three things outside itself:
#   Nmap   - the network scanner program          (scans)
#   Npcap  - the Windows packet-capture driver    (traffic monitoring, nmap)
#   Administrator rights                           (OS detection, traffic)
# This checks each one, so the user is told clearly what is missing instead
# of scans silently failing. Npcap is installed together with Nmap by the
# official Nmap installer.
#
# Flow:  launcher.py logs the results at start-up
#        dashboard.js loadSystemCheck() -> GET /api/system/check
#        (routes/local_network.py) -> a warning bar on the dashboard if needed
# =============================================================================

import ctypes
import os
import platform
import shutil

# where the official Nmap installer puts nmap.exe
NMAP_WINDOWS_PATHS = (
    r"C:\Program Files (x86)\Nmap\nmap.exe",
    r"C:\Program Files\Nmap\nmap.exe",
)
NMAP_DOWNLOAD = "https://nmap.org/download.html#windows"


def find_nmap():
    """Full path to nmap(.exe), or None if it isn't installed."""
    found = shutil.which("nmap")
    if found:
        return found
    if platform.system() == "Windows":
        return next((p for p in NMAP_WINDOWS_PATHS if os.path.isfile(p)), None)
    return None


def npcap_installed():
    """True if the Npcap driver is installed (always True outside Windows,
    where the system's own libpcap is used)."""
    if platform.system() != "Windows":
        return True
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    return os.path.isdir(os.path.join(system32, "Npcap"))


def is_admin():
    """True if LanWatch is running with Administrator (root) rights."""
    try:
        if platform.system() == "Windows":
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def run_checks():
    """All checks as {"ok": bool, "checks": [{id, ok, title, detail}]}."""
    nmap_path = find_nmap()
    checks = [
        {"id": "nmap", "ok": nmap_path is not None,
         "title": "Nmap (network scanner)",
         "detail": f"Found: {nmap_path}" if nmap_path else
                   f"Not installed, so scans can't run. Install it from {NMAP_DOWNLOAD} "
                   "(the installer also installs Npcap), then restart LanWatch."},
        {"id": "npcap", "ok": npcap_installed(),
         "title": "Npcap (packet capture)",
         "detail": "Installed." if npcap_installed() else
                   "Not installed, so traffic can't be measured. It comes with the Nmap installer "
                   f"({NMAP_DOWNLOAD}); keep 'Install Npcap' ticked."},
        {"id": "admin", "ok": is_admin(),
         "title": "Administrator rights",
         "detail": "LanWatch is running as Administrator." if is_admin() else
                   "LanWatch isn't running as Administrator, so it can't measure traffic or "
                   "recognise operating systems. Close it and start it with 'Run as administrator'."},
    ]
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
