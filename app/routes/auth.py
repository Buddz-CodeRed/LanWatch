# =============================================================================
# FILE: app/routes/auth.py  -  SETTINGS LOCK API  (/api/auth)
# -----------------------------------------------------------------------------
# The three addresses behind the Settings page's admin password dialog.
# The rules themselves (password check, idle timeout, wrong-password pauses)
# live in app/auth.py.
#
# Flow:  settings.js checkLock()   -> GET  /api/auth/status -> auth_status()
#        user types the password   -> POST /api/auth/unlock -> unlock_settings()
#        user clicks "Lock settings" -> POST /api/auth/lock -> lock_settings()
# =============================================================================

from flask import Blueprint, jsonify, request

from app import auth

auth_api = Blueprint("auth_api", __name__, url_prefix="/api/auth")


@auth_api.route("/status", methods=["GET"])
def auth_status():
    """GET /api/auth/status - {account_exists, unlocked, idle_minutes}."""
    account = auth.admin_account()
    return jsonify(
        account_exists=account is not None,
        unlocked=auth.is_unlocked(),
        username=account["username"] if account is not None and auth.is_unlocked() else None,
        idle_minutes=auth.UNLOCK_IDLE_MINUTES,
    )


@auth_api.route("/unlock", methods=["POST"])
def unlock_settings():
    """POST /api/auth/unlock - body {"password": "..."}.
    200 unlocked | 403 wrong password | 429 too many attempts (wait_seconds)."""
    password = (request.get_json(silent=True) or {}).get("password")
    ok, message, wait = auth.unlock(password, request.remote_addr or "unknown")
    if ok:
        return jsonify(unlocked=True, message=message)
    return jsonify(error=message, wait_seconds=wait), (429 if wait else 403)


@auth_api.route("/lock", methods=["POST"])
def lock_settings():
    """POST /api/auth/lock - lock settings for this browser straight away."""
    auth.lock()
    return jsonify(unlocked=False)
