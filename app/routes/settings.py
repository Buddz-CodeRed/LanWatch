# =============================================================================
# FILE: app/routes/settings.py  -  SETTINGS AND ACCOUNT API  (/api/settings, /api/account)
# -----------------------------------------------------------------------------
# Stores the System Settings page values and manages the admin account.
#   - settings live in the System_Config table (one key/value row per setting)
#   - the account lives in the Account table (username + one-way password hash)
#
# Flow (settings):  settings.js loadSettings() -> GET /api/settings/ -> read_settings()
#                   user clicks "Save changes" -> settings.js saveScan()/saveTraffic()
#                   -> PUT /api/settings/ -> update_settings() -> save_settings()
#                   Readers elsewhere: services/network_monitor.py (sampling
#                   interval), services/scan_scheduler.py (quick and full scan
#                   intervals, default network), routes/ai.py and
#                   services/scan_summary.py (AI on/off, automatic summaries), app/__init__.py (default network),
#                   utils.js getRefreshMs() (dashboard refresh)
#
# Flow (account):   settings.js loadAccount() -> GET /api/account/ -> read_account()
#                   "Create account" -> POST /api/account/ -> create_first_account()
#                   "Update account" -> PUT /api/account/  -> update_account()
#
# Access control: every route here except GET /api/settings/refresh needs the
# Settings page to be UNLOCKED with the admin password (@settings_unlock_required,
# app/auth.py). Before any admin account exists, the page is open so the owner
# can create one. Changing the account itself also needs the current password.
# =============================================================================
import ipaddress
import re
import sqlite3

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth import settings_unlock_required, unlock_after_account_created
from app.init_database import get_connection

settings_api = Blueprint("settings_api", __name__, url_prefix="/api")


# =============================================================================
# GROUP 1: LIMITS AND DEFAULTS
# The same limits are checked in the browser by settings.js.
# =============================================================================

SESSION_USER_KEY = "user_id"
REQUIRE_SESSION = True    # only an unlocked (signed-in) admin can read or change the account

# Full scans exist, so the Settings page unlocks the "Automatic full scan" box.
FULL_SCAN_AVAILABLE = True

SCAN_INTERVAL_MIN = 5        # a quick scan takes a minute or two, so anything shorter would overlap
FULL_SCAN_INTERVAL_MIN = 60  # a full scan can take up to 30 minutes
SCAN_INTERVAL_MAX = 10080    # one week, in minutes
MIN_PREFIX_LENGTH = 16       # refuse networks bigger than a /16 (65,536 addresses)

TRAFFIC_SAMPLE_MIN, TRAFFIC_SAMPLE_MAX = 1, 300      # seconds between traffic measurements
DASHBOARD_REFRESH_MIN, DASHBOARD_REFRESH_MAX = 2, 300  # seconds between dashboard updates

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
PASSWORD_MIN, PASSWORD_MAX = 8, 128

DEFAULTS = {
    "quick_scan_interval_minutes": "0",
    "full_scan_interval_minutes": "0",
    "default_subnet": "",
    "traffic_sample_interval_seconds": "5",
    "dashboard_refresh_seconds": "5",
    "ai_enabled": "1",              # 1 = the local AI may write explanations
    "ai_auto_summary": "1",         # 1 = write a plain-English summary after each full scan
    "phone_access_enabled": "0",    # 1 = phones/tablets on the home network may open LanWatch
    "phone_access_recognised_only": "1",  # 1 = ...but only devices marked as recognised
}


class ValidationError(Exception):
    """Raised when a submitted value is invalid; its message is shown to the user."""
    pass


# =============================================================================
# GROUP 2: SETTINGS STORAGE
# Read/write System_Config. Also imported by the background services.
# =============================================================================
def get_setting(key, default=None):
    """Return one setting as a string (or `default` if it was never saved)."""
    fallback = DEFAULTS.get(key, default) if default is None else default
    connection = get_connection()
    try:
        row = connection.execute("SELECT value FROM System_Config WHERE key = ?", (key,)).fetchone()
    finally:
        connection.close()
    return row["value"] if row else fallback


def get_all_settings():
    """Every setting (saved value or default), shaped for the Settings page."""
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT key, value FROM System_Config WHERE key IN ({})".format(",".join("?" * len(DEFAULTS))),
            tuple(DEFAULTS),
        ).fetchall()
    finally:
        connection.close()
    stored = {r["key"]: r["value"] for r in rows}
    merged = {k: stored.get(k, v) for k, v in DEFAULTS.items()}

    def number(key):
        """Saved value as a whole number, or the default if unreadable."""
        try:
            return int(merged[key])
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    return {
        "quick_scan_interval_minutes": number("quick_scan_interval_minutes"),
        "full_scan_interval_minutes": number("full_scan_interval_minutes"),
        "default_subnet": merged["default_subnet"],
        "traffic_sample_interval_seconds": number("traffic_sample_interval_seconds"),
        "dashboard_refresh_seconds": number("dashboard_refresh_seconds"),
        "full_scan_available": FULL_SCAN_AVAILABLE,
        "ai_enabled": merged["ai_enabled"] == "1",
        "ai_auto_summary": merged["ai_auto_summary"] == "1",
    }


def get_int_setting(key, default, minimum, maximum):
    """Read a whole-number setting safely for background loops.
    Falls back to `default` if the value is missing or unreadable, and keeps the result
    between `minimum` and `maximum`, so a bad value can never stall or flood the monitor."""
    try:
        value = int(get_setting(key, str(default)))
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def get_bool_setting(key):
    """Read an on/off setting ("1" = on). Used by routes/ai.py and services/scan_summary.py."""
    try:
        return str(get_setting(key)) == "1"
    except Exception:
        return DEFAULTS.get(key) == "1"


def save_settings(values):
    """Insert or update each {key: value}; each setting only ever has one row."""
    connection = get_connection()
    try:
        connection.executemany(
            """INSERT INTO System_Config (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            [(k, str(v)) for k, v in values.items()],
        )
        connection.commit()
    finally:
        connection.close()


# =============================================================================
# GROUP 3: VALIDATION
# Check each submitted value; raise ValidationError with a friendly message.
# =============================================================================
def parse_interval(value, label, minimum=SCAN_INTERVAL_MIN):
    """Scan interval in minutes: 0 (off) or minimum..SCAN_INTERVAL_MAX."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{label} must be a whole number of minutes.")
    try:
        minutes = int(str(value).strip())
    except ValueError:
        raise ValidationError(f"{label} must be a whole number of minutes.")
    if minutes == 0:
        return 0
    if minutes < minimum or minutes > SCAN_INTERVAL_MAX:
        raise ValidationError(
            f"{label} must be 0 (off) or between {minimum} and {SCAN_INTERVAL_MAX} minutes."
        )
    return minutes


def parse_seconds(value, label, minimum, maximum):
    """Whole number of seconds between `minimum` and `maximum`."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{label} must be a whole number of seconds.")
    try:
        seconds = int(str(value).strip())
    except ValueError:
        raise ValidationError(f"{label} must be a whole number of seconds.")
    if seconds < minimum or seconds > maximum:
        raise ValidationError(f"{label} must be between {minimum} and {maximum} seconds.")
    return seconds


def parse_switch(value, label):
    """An on/off value: true/false (or 1/0) -> "1"/"0"."""
    if value in (True, 1, "1", "true"):
        return "1"
    if value in (False, 0, "0", "false"):
        return "0"
    raise ValidationError(f"{label} must be on or off.")


def parse_subnet(value):
    """IPv4 network like 192.168.1.0/24, no bigger than /16. Empty = auto-detect."""
    text = ("" if value is None else str(value)).strip()
    if not text:
        return ""  # empty = detect automatically
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError:
        raise ValidationError("Default network must look like 192.168.1.0/24.")
    if network.version != 4:
        raise ValidationError("Only IPv4 networks are supported.")
    if network.prefixlen < MIN_PREFIX_LENGTH:
        raise ValidationError(f"That network is too large to scan. Use /{MIN_PREFIX_LENGTH} or smaller.")
    return str(network)


# =============================================================================
# GROUP 4: SETTINGS ROUTES
# =============================================================================
@settings_api.route("/settings/", methods=["GET"])
@settings_unlock_required
def read_settings():
    """GET /api/settings/ - all settings (Settings page only: needs unlocking)."""
    return jsonify(get_all_settings())


@settings_api.route("/settings/refresh", methods=["GET"])
def read_refresh_setting():
    """GET /api/settings/refresh - the one setting every page may read without
    unlocking: how often pages refresh (used by utils.js getRefreshMs)."""
    return jsonify(dashboard_refresh_seconds=get_all_settings()["dashboard_refresh_seconds"])


@settings_api.route("/settings/", methods=["PUT"])
@settings_unlock_required
def update_settings():
    """PUT /api/settings/ - save any settings included in the JSON body.

    Every value is validated first; nothing is saved if one is invalid.
    Answers with all settings so the page can show what was stored.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send the settings as JSON."), 400

    to_save = {}
    try:
        if "quick_scan_interval_minutes" in body:
            to_save["quick_scan_interval_minutes"] = parse_interval(
                body["quick_scan_interval_minutes"], "Quick scan interval"
            )
        if "full_scan_interval_minutes" in body:
            full = parse_interval(body["full_scan_interval_minutes"], "Full scan interval",
                                  FULL_SCAN_INTERVAL_MIN)
            if full and not FULL_SCAN_AVAILABLE:
                raise ValidationError("Full scans aren't available yet.")
            to_save["full_scan_interval_minutes"] = full
        if "default_subnet" in body:
            to_save["default_subnet"] = parse_subnet(body["default_subnet"])
        if "traffic_sample_interval_seconds" in body:
            to_save["traffic_sample_interval_seconds"] = parse_seconds(
                body["traffic_sample_interval_seconds"], "Traffic sampling interval",
                TRAFFIC_SAMPLE_MIN, TRAFFIC_SAMPLE_MAX,
            )
        if "dashboard_refresh_seconds" in body:
            to_save["dashboard_refresh_seconds"] = parse_seconds(
                body["dashboard_refresh_seconds"], "Dashboard refresh",
                DASHBOARD_REFRESH_MIN, DASHBOARD_REFRESH_MAX,
            )
        if "ai_enabled" in body:
            to_save["ai_enabled"] = parse_switch(body["ai_enabled"], "AI assistant")
        if "ai_auto_summary" in body:
            to_save["ai_auto_summary"] = parse_switch(body["ai_auto_summary"], "Automatic summaries")
    except ValidationError as err:
        return jsonify(error=str(err)), 400

    if to_save:
        save_settings(to_save)
    return jsonify(get_all_settings())


# =============================================================================
# GROUP 5: ACCOUNT HELPERS
# =============================================================================
def account_count(connection):
    """How many accounts exist (0 means first-run set-up is needed)."""
    return connection.execute("SELECT COUNT(*) AS n FROM Account").fetchone()["n"]


def resolve_user_id(connection):
    """Signed-in user if there is one; otherwise the first admin (unless REQUIRE_SESSION)."""
    user_id = session.get(SESSION_USER_KEY)
    if user_id:
        return user_id
    if REQUIRE_SESSION:
        return None
    row = connection.execute("SELECT user_id FROM Account ORDER BY user_id LIMIT 1").fetchone()
    return row["user_id"] if row else None


def check_username(value):
    """3-32 letters, numbers, dots, dashes or underscores."""
    username = ("" if value is None else str(value)).strip()
    if not USERNAME_RE.match(username):
        raise ValidationError("Username must be 3 to 32 characters: letters, numbers, dots, dashes or underscores.")
    return username


def check_new_password(value):
    """PASSWORD_MIN to PASSWORD_MAX characters."""
    if not isinstance(value, str) or not (PASSWORD_MIN <= len(value) <= PASSWORD_MAX):
        raise ValidationError(f"New password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    return value


# =============================================================================
# GROUP 6: ACCOUNT ROUTES
# =============================================================================

@settings_api.route("/account/", methods=["GET"])
def read_account():
    """GET /api/account/ - username + created date, or 404 setup_required if none.
    Needs unlocking once an account exists."""
    connection = get_connection()
    try:
        if account_count(connection) == 0:
            return jsonify(error="No account exists yet. Create the admin account.", setup_required=True), 404
        from app.auth import is_unlocked, keep_unlocked
        if not is_unlocked():
            return jsonify(error="Settings are locked. Enter the admin password.", locked=True), 401
        keep_unlocked()
        user_id = resolve_user_id(connection)
        if not user_id:
            return jsonify(error="You need to be signed in to view account details."), 401
        row = connection.execute(
            "SELECT username, created_at FROM Account WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return jsonify(error="This account no longer exists."), 404
    return jsonify(username=row["username"], created_at=row["created_at"])


@settings_api.route("/account/", methods=["POST"])
def create_first_account():
    """POST /api/account/ - create the admin account (only while none exists)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send the details as JSON."), 400
    try:
        username = check_username(body.get("username"))
        password = check_new_password(body.get("new_password"))
    except ValidationError as err:
        return jsonify(error=str(err)), 400

    connection = get_connection()
    try:
        if account_count(connection) > 0:
            return jsonify(error="An account already exists. Use Update account to change it."), 409
        cursor = connection.execute(
            "INSERT INTO Account (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        connection.commit()
        new_id = cursor.lastrowid
        row = connection.execute(
            "SELECT username, created_at FROM Account WHERE user_id = ?", (new_id,)
        ).fetchone()
    finally:
        connection.close()

    if current_app.secret_key:  # sessions only work when a secret key is set
        unlock_after_account_created(new_id)   # the creator stays unlocked
    return jsonify(username=row["username"], created_at=row["created_at"]), 201


@settings_api.route("/account/", methods=["PUT"])
@settings_unlock_required
def update_account():
    """PUT /api/account/ - change username and/or password.

    Needs the correct current password (403 if wrong, 409 if the new
    username is already taken).
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send the details as JSON."), 400

    current_password = body.get("current_password")
    if not isinstance(current_password, str) or not current_password:
        return jsonify(error="Enter your current password to save changes."), 400

    connection = get_connection()
    try:
        user_id = resolve_user_id(connection)
        if not user_id:
            return jsonify(error="You need to be signed in to change account details."), 401

        row = connection.execute(
            "SELECT username, password_hash FROM Account WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return jsonify(error="This account no longer exists."), 404
        if not check_password_hash(row["password_hash"], current_password):
            return jsonify(error="Your current password is incorrect."), 403

        updates, params = [], []
        try:
            new_username = body.get("username")
            if new_username is not None and str(new_username).strip() != row["username"]:
                updates.append("username = ?")
                params.append(check_username(new_username))
            if body.get("new_password"):
                updates.append("password_hash = ?")
                params.append(generate_password_hash(check_new_password(body["new_password"])))
        except ValidationError as err:
            return jsonify(error=str(err)), 400

        if not updates:
            return jsonify(error="There's nothing to change."), 400

        try:
            connection.execute(f"UPDATE Account SET {', '.join(updates)} WHERE user_id = ?", (*params, user_id))
            connection.commit()
        except sqlite3.IntegrityError:
            return jsonify(error="That username is already taken."), 409

        if body.get("new_password"):
            # new password: any OTHER browser that was unlocked must unlock again
            from app.auth import end_other_unlocks
            end_other_unlocks()

        fresh = connection.execute(
            "SELECT username, created_at FROM Account WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        connection.close()

    return jsonify(username=fresh["username"], created_at=fresh["created_at"])