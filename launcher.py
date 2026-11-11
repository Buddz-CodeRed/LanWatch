# =============================================================================
# FILE: launcher.py  -  START LANWATCH FOR EVERYDAY USE (Step 43)
# -----------------------------------------------------------------------------
# run.py is for DEVELOPMENT: Flask's built-in server in debug mode (reloads on
# every code change, shows code-running error pages). This file is how
# LanWatch starts for real, and what the installer's LanWatch.exe runs:
#
#   1. sets up a log file when there's no console window to print to
#   2. if LanWatch is already running, just opens the browser and stops
#   3. checks Nmap, Npcap and Administrator rights (services/system_check.py)
#      and writes the result to the log (the dashboard shows it too)
#   4. builds the app (create_app, debug OFF: background workers start once)
#   5. opens http://127.0.0.1:5000 in the browser once the server answers
#   6. serves the app with waitress, a production-ready web server
#
# Run it (as Administrator):   python launcher.py
# Options:  --port 5001        use another port
#           --no-browser       don't open the browser
#
# Modes used by the Windows installer (Mrk1-Installer/LanWatch.iss):
#   --create-admin FILE  create (or replace) the admin account from FILE
#                        (line 1 username, line 2 password), then delete FILE
#   --background         run without opening the browser (the sign-in task)
#   --open               open LanWatch in the browser; if it isn't running,
#                        start it through the sign-in task first (the shortcuts)
#
# LanWatch listens on 127.0.0.1, so only this computer can open it. Phones and
# tablets on the home network can be allowed in Settings: that adds a second,
# separate listener (app/services/phone_listener.py) with its own sign-in.
# =============================================================================

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from app.paths import data_path, is_installed

HOST = "127.0.0.1"
DEFAULT_PORT = 5000
TASK_NAME = "LanWatch"            # the Windows sign-in task the installer creates
LOG_MAX_BYTES = 5 * 1024 * 1024   # start a new log file after 5 MB...
LOG_KEEP = 3                      # ...keeping the 3 most recent old ones


# =============================================================================
# GROUP 1: LOG FILE
# =============================================================================

def setup_log_file():
    """With no console window (the installed app), send everything LanWatch
    prints to  <data folder>\\logs\\lanwatch.log  instead of losing it."""
    if sys.stdout is not None and not is_installed():
        return None                                   # a normal console: print there
    try:
        return _open_log_file()
    except OSError:
        # e.g. no permission to write the log: carry on without one
        sys.stdout = sys.stderr = open(os.devnull, "w")
        return None


def _open_log_file():
    log_path = data_path("logs", "lanwatch.log")
    if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_MAX_BYTES:
        for i in range(LOG_KEEP - 1, 0, -1):          # lanwatch.log.2 -> .3, .1 -> .2
            older = f"{log_path}.{i}"
            if os.path.exists(older):
                os.replace(older, f"{log_path}.{i + 1}")
        os.replace(log_path, f"{log_path}.1")
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    return log_path


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


# =============================================================================
# GROUP 2: ONLY ONE COPY AT A TIME
# =============================================================================

def port_in_use(port):
    """True if something is already listening on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((HOST, port)) == 0


def lanwatch_answers(port):
    """True if the program on that port is LanWatch (it knows /api/auth/status)."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/auth/status", timeout=3) as reply:
            return reply.status == 200
    except Exception:
        return False


def running_copy_has_admin(port):
    """True if the LanWatch already running on <port> has Administrator rights
    (it reports this in its own system check)."""
    try:
        import json
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/network/system-check", timeout=5) as reply:
            checks = json.load(reply).get("checks", [])
        return any(c.get("id") == "admin" and c.get("ok") for c in checks)
    except Exception:
        return False


def stop_copy_on_port(port, wait_seconds=15):
    """Stop the LanWatch listening on <port> (used to replace a copy that has
    no Administrator rights with one that has). Returns True once the port is free."""
    import psutil
    for connection in psutil.net_connections(kind="tcp"):
        if connection.laddr and connection.laddr.port == port and connection.status == psutil.CONN_LISTEN and connection.pid:
            try:
                process = psutil.Process(connection.pid)
                log(f"Stopping the LanWatch without Administrator rights ({process.name()}, process {process.pid}).")
                process.terminate()
            except psutil.Error as error:
                log(f"Couldn't stop it: {error}")
    end = time.time() + wait_seconds
    while time.time() < end:
        if not port_in_use(port):
            return True
        time.sleep(0.3)
    return False


# =============================================================================
# GROUP 3: OPEN THE BROWSER WHEN READY
# =============================================================================

def open_browser_when_ready(url, port, wait_seconds=30):
    """Wait (in the background) until the server answers, then open the browser."""
    def waiter():
        end = time.time() + wait_seconds
        while time.time() < end:
            if port_in_use(port):
                webbrowser.open(url)
                return
            time.sleep(0.3)
        log("The server didn't start in time, so the browser wasn't opened.")
    threading.Thread(target=waiter, daemon=True, name="OpenBrowser").start()


# =============================================================================
# GROUP 4: INSTALLER HELPERS (--create-admin, --open)
# =============================================================================

def create_admin_from_file(path):
    """Create the admin account, or replace it (reinstall / forgotten password),
    from a file with the username on line 1 and the password on line 2.
    The file is deleted straight away. Returns an exit code (0 = OK)."""
    try:
        # utf-8-sig: the installer writes UTF-8 (with a BOM), so any characters work
        with open(path, encoding="utf-8-sig") as file:
            lines = file.read().splitlines()
    except OSError as error:
        log(f"Couldn't read the admin details: {error}")
        return 2
    finally:
        try:
            os.remove(path)                           # never leave the password on disk
        except OSError:
            pass
    if len(lines) < 2:
        log("The admin details file needs a username and a password.")
        return 2

    from werkzeug.security import generate_password_hash
    from app.init_database import get_connection, init_db
    from app.routes.settings import ValidationError, check_new_password, check_username
    try:
        username, password = check_username(lines[0]), check_new_password(lines[1])
    except ValidationError as error:
        log(f"Admin account not created: {error}")
        return 3

    init_db()                                         # first install: create the tables
    connection = get_connection()
    try:
        existing = connection.execute("SELECT user_id FROM Account ORDER BY user_id LIMIT 1").fetchone()
        if existing:
            connection.execute("UPDATE Account SET username = ?, password_hash = ? WHERE user_id = ?",
                               (username, generate_password_hash(password), existing["user_id"]))
        else:
            connection.execute("INSERT INTO Account (username, password_hash) VALUES (?, ?)",
                               (username, generate_password_hash(password)))
        connection.commit()
    finally:
        connection.close()
    log(f"Admin account '{username}' {'updated' if existing else 'created'}.")
    return 0


def open_lanwatch(url, port):
    """The shortcuts: open LanWatch in the browser, starting it first if needed.
    Starting goes through the sign-in task, which already has Administrator
    rights, so no permission prompt appears. A copy that is running WITHOUT
    Administrator rights (e.g. started by a double-click) is replaced."""
    if lanwatch_answers(port) and not running_copy_has_admin(port):
        stop_copy_on_port(port)
    if not lanwatch_answers(port):
        import subprocess
        started = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                                 capture_output=True, creationflags=0x08000000).returncode == 0
        if not started:
            # no task (or not allowed to run it): start LanWatch with Windows'
            # "Run as administrator" prompt instead
            started = _start_elevated(port)
        if not started:
            log("LanWatch couldn't be started. Reinstall it, or start launcher.py yourself.")
            return 1
        end = time.time() + 60
        while time.time() < end and not lanwatch_answers(port):
            time.sleep(0.5)
    webbrowser.open(url)
    return 0


def _start_elevated(port):
    """Start this program in --background mode with the Administrator prompt."""
    try:
        import ctypes
        if is_installed():
            program, arguments = sys.executable, f"--background --port {port}"
        else:
            program, arguments = sys.executable, f'"{os.path.abspath(__file__)}" --background --port {port}'
        # ShellExecute "runas" = Windows' "Run as administrator"; > 32 means it started
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", program, arguments, None, 0) > 32
    except Exception:
        return False


# =============================================================================
# GROUP 5: START-UP
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Start LanWatch")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--background", action="store_true", help="run without opening the browser (sign-in task)")
    parser.add_argument("--open", action="store_true", help="open LanWatch, starting it first if needed")
    parser.add_argument("--create-admin", metavar="FILE", help="create the admin account from FILE")
    options = parser.parse_args()
    url = f"http://{HOST}:{options.port}/"

    if sys.stdout is None:                            # no console (installed app)
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if options.create_admin:
        return create_admin_from_file(options.create_admin)
    if options.open:
        return open_lanwatch(url, options.port)
    if options.background:
        options.no_browser = True

    # The installed LanWatch must run with Administrator rights (to scan and
    # measure traffic). Started without them (e.g. a double-click on
    # LanWatch.exe)? Start the proper copy instead: through the sign-in task,
    # or Windows' "Run as administrator" prompt.
    from app.services.system_check import is_admin
    have_admin = is_admin()
    if is_installed() and not have_admin:
        return open_lanwatch(url, options.port)

    log_path = setup_log_file()
    log("Starting LanWatch" + (f" (log file: {log_path})" if log_path else ""))

    # 2. already running? then just show it - unless THIS copy has
    #    Administrator rights and the running one doesn't: then replace it
    if port_in_use(options.port):
        if lanwatch_answers(options.port):
            if have_admin and not running_copy_has_admin(options.port):
                log("The running LanWatch has no Administrator rights; replacing it with this one.")
                if not stop_copy_on_port(options.port):
                    log("It couldn't be stopped. Close it (Task Manager) and start LanWatch again.")
                    return 1
            else:
                log(f"LanWatch is already running at {url}; opening it.")
                if not options.no_browser:
                    webbrowser.open(url)
                return 0
        else:
            log(f"Port {options.port} is used by another program. Start LanWatch with --port <another number>.")
            return 1

    # 3. what's missing? (also shown on the dashboard)
    from app.services.system_check import run_checks
    for check in run_checks()["checks"]:
        log(f"Check - {check['title']}: {'OK' if check['ok'] else 'MISSING'}. {check['detail']}")

    # 4. the app itself (debug is off, so the background workers start once)
    #    (the phone listener uses the same port: services/phone_listener.py)
    os.environ["LANWATCH_PORT"] = str(options.port)
    from app import create_app
    from app.init_database import DATABASE
    app = create_app()
    log(f"Data: {DATABASE}")

    # 5 + 6. open the browser once ready, then serve until the window/process is closed
    if not options.no_browser:
        open_browser_when_ready(url, options.port)
    log(f"LanWatch is running at {url}  (press Ctrl+C to stop)")

    from waitress import serve
    try:
        serve(app, host=HOST, port=options.port, threads=8, ident="LanWatch")
    except KeyboardInterrupt:
        pass
    log("LanWatch stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
