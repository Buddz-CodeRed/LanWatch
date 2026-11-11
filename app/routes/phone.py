# =============================================================================
# FILE: app/routes/phone.py  -  PHONES AND TABLETS  (/phone-login, /api/phone)
# -----------------------------------------------------------------------------
# On the phone:
#   GET  /phone-login         the sign-in page (templates/phone_login.html)
#   POST /api/phone/login     {username, password} -> sign-in cookie
#   POST /phone-logout        "Sign out" in the phone's menu
# On the Settings page (all need Settings unlocked with the admin password):
#   GET  /api/phone/status    on/off, the address for phones, problems
#   PUT  /api/phone/settings  {enabled, recognised_only}
#   GET  /api/phone/qr.svg    QR code of the address
#   POST /api/phone/waiting/<device_id>/allow  let a waiting phone in
#   GET  /api/phone/sessions  signed-in phones and tablets
#   DELETE /api/phone/sessions/<id>  (or /api/phone/sessions = all) sign out
#
# Rules: app/phone_access.py. Listener: services/phone_listener.py.
# =============================================================================

from flask import Blueprint, Response, jsonify, make_response, redirect, render_template, request

from app import phone_access
from app.auth import settings_unlock_required

phone = Blueprint("phone", __name__)


# =============================================================================
# GROUP 1: ON THE PHONE
# =============================================================================

@phone.route("/phone-login", methods=["GET"])
def phone_login_page():
    """The sign-in page. On this computer itself there is nothing to sign in to."""
    target = phone_access.safe_next(request.args.get("next", "/"))
    if not phone_access.is_remote() or phone_access.current_session():
        return redirect(target)
    return render_template("phone_login.html", next=target)


@phone.route("/api/phone/login", methods=["POST"])
def phone_login():
    """POST /api/phone/login - body {"username", "password"}.
    200 signed in | 403 wrong details | 429 too many tries (wait_seconds)."""
    if not phone_access.is_remote():
        return jsonify(signed_in=True, message="This is the computer LanWatch runs on: no sign-in needed.")
    body = request.get_json(silent=True) or {}
    token, message, wait = phone_access.sign_in(body.get("username"), body.get("password"))
    if token is None:
        return jsonify(error=message, wait_seconds=wait), (429 if wait else 403)
    return phone_access.set_cookie(make_response(jsonify(signed_in=True)), token)


@phone.route("/phone-logout", methods=["POST"])
def phone_logout():
    """'Sign out' in the phone's menu: forget this phone's sign-in."""
    return phone_access.sign_out_this_device(redirect("/phone-login"))


# =============================================================================
# GROUP 2: SETTINGS PAGE
# =============================================================================

def _status():
    from app.services.phone_listener import status
    from app.routes.settings import get_bool_setting
    result = status()
    result["recognised_only"] = get_bool_setting("phone_access_recognised_only")
    result["this_device_is_remote"] = phone_access.is_remote()
    result["waiting"] = phone_access.waiting_devices() if result["enabled"] else []
    return result


@phone.route("/api/phone/status", methods=["GET"])
@settings_unlock_required
def phone_status():
    return jsonify(_status())


@phone.route("/api/phone/settings", methods=["PUT"])
@settings_unlock_required
def phone_settings():
    """PUT /api/phone/settings - body {"enabled": true/false, "recognised_only": true/false}."""
    from app.routes.settings import ValidationError, parse_switch, save_settings
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send the settings as JSON."), 400
    to_save = {}
    try:
        if "enabled" in body:
            to_save["phone_access_enabled"] = parse_switch(body["enabled"], "Phones and tablets")
        if "recognised_only" in body:
            to_save["phone_access_recognised_only"] = parse_switch(body["recognised_only"], "Recognised devices only")
    except ValidationError as err:
        return jsonify(error=str(err)), 400
    if to_save:
        save_settings(to_save)
        from app.services.phone_listener import sync
        sync()                                   # start / stop straight away
    return jsonify(_status())


@phone.route("/api/phone/qr.svg", methods=["GET"])
@settings_unlock_required
def phone_qr():
    """The address for phones as a QR code (made on this computer, no internet)."""
    url = _status()["url"]
    if not url:
        return jsonify(error="Phone access isn't running."), 404
    import io
    import segno
    svg = io.BytesIO()        # a complete SVG file (with xmlns), so <img> can show it
    segno.make(url, error="m").save(svg, kind="svg", scale=6, border=2, dark="#090D16", light="#FFFFFF")
    return Response(svg.getvalue(), mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})


@phone.route("/api/phone/waiting/<int:device_id>/allow", methods=["POST"])
@settings_unlock_required
def phone_allow(device_id):
    """"Allow" next to a device waiting to be let in (= "I know this device")."""
    if not phone_access.allow_device(device_id):
        return jsonify(error="That device wasn't found."), 404
    return jsonify(allowed=device_id)


@phone.route("/api/phone/sessions", methods=["GET"])
@settings_unlock_required
def phone_sessions():
    current = phone_access.current_session()
    sessions = phone_access.list_sessions()
    for s in sessions:
        s["this_device"] = bool(current and current["session_id"] == s["session_id"])
    return jsonify(sessions=sessions)


@phone.route("/api/phone/sessions/<int:session_id>", methods=["DELETE"])
@settings_unlock_required
def phone_sign_out_one(session_id):
    if not phone_access.sign_out_session(session_id):
        return jsonify(error="That sign-in wasn't found."), 404
    return jsonify(signed_out=1)


@phone.route("/api/phone/sessions", methods=["DELETE"])
@settings_unlock_required
def phone_sign_out_all():
    return jsonify(signed_out=phone_access.sign_out_session(None))
