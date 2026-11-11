# =============================================================================
# FILE: app/paths.py  -  WHERE LANWATCH'S FILES LIVE
# -----------------------------------------------------------------------------
# LanWatch has two kinds of files, kept in two different places:
#
#   APP FILES  (read-only: pages, scripts, knowledge base, AI model)
#     resource_path("app", "knowledge")
#       development:    the project folder   C:\...\LanWatch_Mrk1\
#       installed app:  inside the program   C:\Program Files\LanWatch\_internal\
#                       (PyInstaller unpacks them to sys._MEIPASS)
#
#   YOUR DATA  (written while running: database, logs)
#     data_path("lanwatch.db")
#       1. the LANWATCH_DATA environment variable, if set (handy for tests:
#          point it at a scratch folder and your real data is never touched)
#       2. installed app:  C:\ProgramData\LanWatch\   (shared by all Windows
#                          users; Program Files can't be written to)
#       3. development:    the project folder, as before
#
# Keeping data out of the program folder means updating or reinstalling
# LanWatch never touches your data, and the database is always found no
# matter which folder LanWatch is started from.
#
# Used by: init_database.py (database), services/ai_translator.py (model),
#          services/knowledge.py (knowledge base), launcher.py (logs)
# =============================================================================

import os
import sys

APP_NAME = "LanWatch"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_installed():
    """True when running as the installed program (built by PyInstaller)."""
    return getattr(sys, "frozen", False)


def resource_path(*parts):
    """Path to a read-only file that ships with LanWatch."""
    base = getattr(sys, "_MEIPASS", None) or PROJECT_DIR
    return os.path.join(base, *parts)


def data_dir():
    """The folder for LanWatch's own data (created if missing)."""
    folder = os.environ.get("LANWATCH_DATA")
    if not folder:
        if is_installed():
            root = os.environ.get("PROGRAMDATA") or os.path.expanduser("~")
            folder = os.path.join(root, APP_NAME)
        else:
            folder = PROJECT_DIR
    os.makedirs(folder, exist_ok=True)
    return folder


def data_path(*parts):
    """Path to a file in LanWatch's data folder (its sub-folder is created if needed)."""
    path = os.path.join(data_dir(), *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
