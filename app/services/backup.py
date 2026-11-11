# =============================================================================
# FILE: app/services/backup.py  -  BACKING UP AND RESTORING YOUR DATA
# -----------------------------------------------------------------------------
# Everything LanWatch has learned lives in one file, lanwatch.db. If it is
# deleted or damaged, the device history, the learned "normal" for the
# network, the alerts and the settings are gone. This file keeps copies.
#
#   Back up now   create_backup()  a copy in the "backups" folder next to the
#                                  database (C:\ProgramData\LanWatch\backups
#                                  when installed). The newest KEEP_BACKUPS
#                                  are kept; older ones are removed.
#   Restore       restore_backup() puts a copy back. Before anything changes:
#                                  - the copy is checked (check_backup)
#                                  - the CURRENT data is backed up first
#                                    ("before-restore"), so a restore can be undone
#   Add a file    save_uploaded()  a backup file from somewhere else (a USB
#                                  stick, another computer) is checked and
#                                  added to the list; it is only used once
#                                  you click Restore on it.
#
# Why SQLite's own backup and not a file copy: LanWatch writes to the
# database every few seconds (traffic sampling). Copying the file in the
# middle of a write could give a broken copy. connection.backup() always
# gives a complete, consistent copy, and restoring the same way means the
# database is swapped safely while LanWatch keeps running.
#
# Flow:  Settings page, "Backups" (js/backups.js) -> routes/backup.py -> here
# =============================================================================

import os
import re
import sqlite3
import threading
from datetime import datetime

from app.init_database import DATABASE, cleanup_interrupted_sessions, get_connection, init_db
from app.paths import data_dir

KEEP_BACKUPS = 20                       # newest copies kept; older ones are removed
MAX_UPLOAD_BYTES = 500 * 1024 * 1024    # a real lanwatch.db is a few MB
NEEDED_TABLES = {"Devices", "Scans", "System_Config", "Account"}   # proves it's a LanWatch database

# backup file names LanWatch makes:  lanwatch-2026-11-12_14-05-33-manual.db
NAME_RE = re.compile(r"^lanwatch-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-[a-z-]+\.db$")
REASONS = {
    "manual": "Backed up by you",
    "before-restore": "Saved automatically before a restore",
    "uploaded": "Added from a file",
}

_busy = threading.Lock()                # one backup or restore at a time


class BackupError(Exception):
    """Something the user should be told (the message is shown on the page)."""


# =============================================================================
# GROUP 1: WHERE BACKUPS LIVE
# =============================================================================

def backup_dir():
    folder = os.path.join(data_dir(), "backups")
    os.makedirs(folder, exist_ok=True)
    return folder


def _new_path(reason):
    """A new, unused backup file name for this moment."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(backup_dir(), f"lanwatch-{stamp}-{reason}.db")
    n = 1
    while os.path.exists(path):          # two in the same second
        n += 1
        stamp2 = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(backup_dir(), f"lanwatch-{stamp2}-{reason}-{'x' * n}.db")
    return path


def path_for(name):
    """The full path of a backup named in a request. Only plain backup names
    in the backups folder are accepted (no "..\\" tricks)."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise BackupError("That isn't a LanWatch backup.")
    path = os.path.join(backup_dir(), name)
    if not os.path.isfile(path):
        raise BackupError("That backup no longer exists.")
    return path


def _prune():
    """Keep only the newest KEEP_BACKUPS copies."""
    names = sorted((n for n in os.listdir(backup_dir()) if NAME_RE.match(n)), reverse=True)
    for old in names[KEEP_BACKUPS:]:
        try:
            os.remove(os.path.join(backup_dir(), old))
        except OSError:
            pass


# =============================================================================
# GROUP 2: CHECKING A BACKUP
# =============================================================================

def check_backup(path):
    """Make sure a file is a complete, undamaged LanWatch database.
    Returns {"devices": n, "scans": n}; raises BackupError if not."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        raise BackupError("The file couldn't be opened as a database.")
    try:
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError:
            raise BackupError("That file isn't a LanWatch backup (it isn't a database).")
        if result != "ok":
            raise BackupError("That backup is damaged, so it can't be used.")
        objects = conn.execute("SELECT type, name FROM sqlite_master").fetchall()
        tables = {name for kind, name in objects if kind == "table"}
        if not NEEDED_TABLES <= tables:
            raise BackupError("That database isn't from LanWatch.")
        # a LanWatch database has no triggers and only the Baseline view;
        # anything else could make the restored data behave unexpectedly
        if any(kind == "trigger" or (kind == "view" and name != "Baseline") for kind, name in objects):
            raise BackupError("That database contains things LanWatch never creates, so it isn't used.")
        return {
            "devices": conn.execute("SELECT COUNT(*) FROM Devices").fetchone()[0],
            "scans": conn.execute("SELECT COUNT(*) FROM Scans").fetchone()[0],
        }
    finally:
        conn.close()


# =============================================================================
# GROUP 3: LISTING AND MAKING BACKUPS
# =============================================================================

def list_backups():
    """Every backup, newest first, with what's in it."""
    items = []
    for name in sorted((n for n in os.listdir(backup_dir()) if NAME_RE.match(n)), reverse=True):
        path = os.path.join(backup_dir(), name)
        reason = name[len("lanwatch-2026-11-12_14-05-33-"):-3].rstrip("-x")
        try:
            counts = check_backup(path)
            problem = None
        except BackupError as err:
            counts, problem = {"devices": None, "scans": None}, str(err)
        when = datetime.strptime(name[9:28], "%Y-%m-%d_%H-%M-%S")
        items.append({
            "name": name,
            "created": when.strftime("%Y-%m-%dT%H:%M:%S"),     # this computer's local time
            "reason": REASONS.get(reason, reason),
            "size": os.path.getsize(path),
            "devices": counts["devices"],
            "scans": counts["scans"],
            "problem": problem,
        })
    return items


def create_backup(reason="manual"):
    """Copy the live database into a new backup file. Returns its name."""
    path = _new_path(reason)
    source = get_connection()
    target = sqlite3.connect(path)
    try:
        source.backup(target)            # a complete copy, even while LanWatch is writing
    finally:
        target.close()
        source.close()
    try:
        check_backup(path)
    except BackupError:
        os.remove(path)
        raise BackupError("The backup didn't come out right, so it was removed. Try again.")
    _prune()
    return os.path.basename(path)


def backup_now():
    with _busy:
        return create_backup("manual")


def save_uploaded(stream):
    """Save a backup file sent from the browser, check it, and add it to the list."""
    path = _new_path("uploaded")
    written = 0
    with _busy:
        try:
            with open(path, "wb") as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise BackupError("That file is far too big to be a LanWatch backup.")
                    out.write(chunk)
            if written == 0:
                raise BackupError("That file is empty.")
            check_backup(path)
        except BaseException:
            if os.path.exists(path):
                os.remove(path)
            raise
        _prune()
    return os.path.basename(path)


# =============================================================================
# GROUP 4: RESTORING
# =============================================================================

def _scan_running():
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM Scans WHERE scan_status IN ('pending', 'running') LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def restore_backup(name):
    """Replace the live data with a backup. The current data is backed up first.
    Returns the name of that safety copy."""
    path = path_for(name)
    with _busy:
        check_backup(path)
        if _scan_running():
            raise BackupError("A scan is running. Wait for it to finish, then restore.")

        safety = create_backup("before-restore")

        # keep this computer's session key, so the cookie signing doesn't change
        conn = get_connection()
        try:
            row = conn.execute("SELECT value FROM System_Config WHERE key = 'session_secret'").fetchone()
        finally:
            conn.close()

        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        target = sqlite3.connect(DATABASE, timeout=30)
        try:
            source.backup(target)        # swaps the contents in one step
        finally:
            source.close()
            target.close()

        if row:
            conn = get_connection()
            try:
                conn.execute(
                    """INSERT INTO System_Config (key, value) VALUES ('session_secret', ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (row["value"],))
                conn.commit()
            finally:
                conn.close()

        # an older backup may be missing newer tables or columns: add them;
        # a backup made during a scan says "running": mark it interrupted
        init_db()
        cleanup_interrupted_sessions()
    return safety
