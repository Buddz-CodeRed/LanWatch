# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# FILE: Mrk1-Installer/LanWatch.spec  -  PYINSTALLER RECIPE (BUILD_GUIDE Step 44)
# -----------------------------------------------------------------------------
# Turns the Python project into a normal Windows program folder that runs on
# computers WITHOUT Python:
#
#   dist/LanWatch/
#     LanWatch.exe        <- launcher.py, as a program (no console window)
#     _internal/          <- Python itself, Flask, scapy, llama-cpp, ...
#       app/templates, app/static, app/knowledge   (pages, styles, facts)
#       models/           (the AI model, ~1.1 GB, and its licence)
#
# Build it with build_installer.ps1 (or: pyinstaller Mrk1-Installer\LanWatch.spec)
#
# Notes:
#   - "onedir" (a folder), not one big .exe: much faster to start, which
#     matters with a 1 GB AI model inside.
#   - No "--uac-admin": LanWatch.exe itself starts without a permission prompt.
#     Administrator rights come from the sign-in task the installer creates
#     (run with highest privileges), so the user isn't asked at every sign-in.
#   - app/paths.py finds these files through sys._MEIPASS (= _internal).
# =============================================================================
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(SPECPATH)                      # the project folder

datas = [
    (os.path.join(ROOT, "app", "templates"), os.path.join("app", "templates")),
    (os.path.join(ROOT, "app", "static"), os.path.join("app", "static")),
    (os.path.join(ROOT, "app", "knowledge"), os.path.join("app", "knowledge")),
    (os.path.join(ROOT, "models", "qwen2.5-1.5b-instruct-q4_k_m.gguf"), "models"),
    (os.path.join(ROOT, "models", "LICENSE-Qwen2.5-1.5B-Instruct.txt"), "models"),
]
binaries = []
hiddenimports = [
    "waitress",
    # imported inside functions, listed here to be safe
    "app.routes.ai", "app.routes.alerts", "app.routes.auth", "app.routes.devices",
    "app.routes.findings", "app.routes.local_network", "app.routes.scans",
    "app.routes.settings", "app.routes.usage",
    "app.services.ai_translator", "app.services.findings", "app.services.knowledge",
    "app.services.scan_summary", "app.services.system_check",
    "app.routes.phone", "app.services.phone_listener", "app.services.router_advice",
    "segno",                                   # QR code for phones (Settings)
    "app.routes.backup", "app.services.backup",  # Settings -> Backups
]

# packages that load parts of themselves in ways PyInstaller can't see
for package in ("llama_cpp", "scapy"):
    d, b, h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide6", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LanWatch",
    icon=os.path.join(SPECPATH, "assets", "lanwatch.ico"),
    console=False,                                    # no black console window
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LanWatch", upx=False)
