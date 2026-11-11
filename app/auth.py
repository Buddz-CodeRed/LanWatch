# =============================================================================
# FILE: app/auth.py  -  ADMIN PASSWORD LOCK FOR THE SETTINGS PAGE
# -----------------------------------------------------------------------------
# The System Settings page (and its API) is locked behind the admin password.
# Entering the password "unlocks" settings for this browser until it has been
# unused for UNLOCK_IDLE_MINUTES; "Lock settings" locks it again straight away.
#
# How it works:
#   - Unlocking creates a random "unlock ticket". The SERVER keeps the list of
#     valid tickets and when each expires (_tickets); the browser only holds
#     its ticket in Flask's signed "session" cookie (signed with
#     get_secret_key, so it can't be forged).
#   - Keeping the list on the server matters: locking deletes the ticket, so
#     a reply that was already on its way with an older cookie can't undo the
#     lock. (Found in testing: with the unlock stored only in the cookie,
#     "Lock settings" could be silently reversed.)
#   - Every protected API route has @settings_unlock_required. It answers
#     401 {"locked": true} unless the browser's ticket is valid, and each
#     allowed request pushes the ticket's expiry forward (so it only locks when
#     idle). Restarting LanWatch clears all tickets: everyone must unlock again.
#   - Before any admin account exists (first run), settings are open, so the
#     owner can create the admin password on the Settings page.
#   - 5 wrong passwords from one computer -> a pause of 1 minute, then 2, 4...
#
# Flow:  settings.html opens -> settings.js checkLock() -> GET /api/auth/status
#        locked? -> password dialog -> POST /api/auth/unlock -> unlock()
#        -> settings.js loads the settings (GET /api/settings/ etc.)
#
# Used by: app/__init__.py (secret key), routes/auth.py (status/unlock/lock),
#          routes/settings.py (protects the settings and account routes),
#          app/phone_access.py (phone sign-in uses check_admin_password)
# =============================================================================

import secrets
import threading
import time
from functools import wraps

from flask import jsonify, request, session
from werkzeug.security import check_password_hash

from app.init_database import get_connection

UNLOCK_IDLE_MINUTES = 15          # settings lock again after this long unused
MAX_FAILED_ATTEMPTS = 5           # wrong passwords before a pause
FIRST_PAUSE_SECONDS = 60          # first pause; doubles each time after that

SESSION_USER_KEY = "user_id"
SESSION_TICKET_KEY = "unlock_ticket"

_tickets = {}                  # ticket -> {"user_id": ..., "expires": time}
_tickets_lock = threading.Lock()


# =============================================================================
# GROUP 1: THE SECRET KEY (signs the session cookie)
# =============================================================================

def get_secret_key():
    """A random secret, created on first run and kept in System_Config, so
    browsers stay unlocked across restarts (until their unlock expires)."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT value FROM System_Config WHERE key = 'session_secret'").fetchone()
        if row:
            return row["value"]
        secret = secrets.token_hex(32)
        connection.execute(
            "INSERT INTO System_Config (key, value) VALUES ('session_secret', ?)", (secret,))
        connection.commit()
        return secret
    finally:
        connection.close()


# =============================================================================
# GROUP 2: IS SETTINGS UNLOCKED?
# =============================================================================

def admin_account():
    """The admin account row (user_id, username, password_hash), or None if none exists yet."""
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT user_id, username, password_hash FROM Account ORDER BY user_id LIMIT 1").fetchone()
    finally:
        connection.close()


def _new_ticket(user_id):
    """Start an unlock: a fresh random ticket, remembered on the server."""
    ticket = secrets.token_urlsafe(32)
    with _tickets_lock:
        _tickets[ticket] = {"user_id": user_id, "expires": time.time() + UNLOCK_IDLE_MINUTES * 60}
    session.permanent = True
    session[SESSION_TICKET_KEY] = ticket
    session[SESSION_USER_KEY] = user_id


def _valid_ticket():
    """This browser's ticket entry if it is still valid, else None (expired ones are removed)."""
    ticket = session.get(SESSION_TICKET_KEY)
    if not ticket:
        return None
    with _tickets_lock:
        entry = _tickets.get(ticket)
        if entry and time.time() >= entry["expires"]:
            del _tickets[ticket]
            entry = None
    return entry


def is_unlocked():
    """True if no admin account exists yet, or this browser holds a valid unlock ticket."""
    account = admin_account()
    if account is None:
        return True
    entry = _valid_ticket()
    return entry is not None and entry["user_id"] == account["user_id"]


def keep_unlocked():
    """Push this browser's idle timer forward (called on every allowed request)."""
    entry = _valid_ticket()
    if entry is not None:
        with _tickets_lock:
            entry["expires"] = time.time() + UNLOCK_IDLE_MINUTES * 60


def end_other_unlocks():
    """After the admin password changes: every OTHER browser must unlock again."""
    mine = session.get(SESSION_TICKET_KEY)
    with _tickets_lock:
        for ticket in [t for t in _tickets if t != mine]:
            del _tickets[ticket]


def settings_unlock_required(view):
    """Decorator for API routes that read or change settings."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_unlocked():
            return jsonify(error="Settings are locked. Enter the admin password.", locked=True), 401
        keep_unlocked()
        return view(*args, **kwargs)
    return wrapper


# =============================================================================
# GROUP 3: UNLOCKING AND LOCKING
# =============================================================================

_failures = {}                     # computer address -> (wrong attempts, paused until, pauses so far)
_failures_lock = threading.Lock()


def seconds_paused(address):
    """How long this computer must still wait after too many wrong passwords (0 = none)."""
    with _failures_lock:
        _, paused_until, _ = _failures.get(address, (0, 0, 0))
    return max(0, int(paused_until - time.time() + 0.999))


def check_admin_password(password, address, username=None):
    """Check the admin password (and username, if given), counting wrong tries
    per computer/phone address. Returns (account or None, message, wait_seconds).
    Shared by unlock() below and the phone sign-in (app/phone_access.py)."""
    wait = seconds_paused(address)
    if wait:
        return None, f"Too many wrong passwords. Try again in {wait} seconds.", wait

    account = admin_account()
    if account is None:
        return None, "No admin account exists yet. Create it on the computer LanWatch runs on.", 0

    name_ok = username is None or (isinstance(username, str)
                                   and username.strip().lower() == account["username"].lower())
    if name_ok and isinstance(password, str) and password and check_password_hash(account["password_hash"], password):
        with _failures_lock:
            _failures.pop(address, None)
        return account, "", 0

    wrong = "That username or password is incorrect." if username is not None else "That password is incorrect."
    with _failures_lock:
        count, _, pauses = _failures.get(address, (0, 0, 0))
        count += 1
        if count >= MAX_FAILED_ATTEMPTS:
            pause = FIRST_PAUSE_SECONDS * (2 ** pauses)
            _failures[address] = (0, time.time() + pause, pauses + 1)
            return None, f"Too many wrong passwords. Try again in {pause} seconds.", pause
        _failures[address] = (count, 0, pauses)
    left = MAX_FAILED_ATTEMPTS - count
    return None, f"{wrong} {left} attempt{'s' if left != 1 else ''} left before a pause.", 0


def unlock(password, address):
    """Check the admin password. Returns (ok, message, wait_seconds)."""
    if admin_account() is None:
        return True, "No admin password is set yet.", 0
    account, message, wait = check_admin_password(password, address)
    if account is None:
        return False, message, wait
    _new_ticket(account["user_id"])
    return True, "Settings unlocked.", 0


def lock():
    """Lock settings for this browser straight away (the ticket stops working
    on the server, whatever cookie the browser still has)."""
    ticket = session.pop(SESSION_TICKET_KEY, None)
    session.pop(SESSION_USER_KEY, None)
    if ticket:
        with _tickets_lock:
            _tickets.pop(ticket, None)


def unlock_after_account_created(user_id):
    """The person who just created the admin account stays unlocked."""
    _new_ticket(user_id)
