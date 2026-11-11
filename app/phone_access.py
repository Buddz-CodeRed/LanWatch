# =============================================================================
# FILE: app/phone_access.py  -  WHO MAY USE LANWATCH FROM A PHONE OR TABLET
# -----------------------------------------------------------------------------
# On this computer (127.0.0.1) nothing changes: LanWatch opens as before.
# Any request from ANOTHER device (it can only arrive through the home-network
# listener, services/phone_listener.py) must pass the gate below first:
#
#   1. "Phones and tablets" must be switched on in Settings
#   2. if "Recognised devices only" is on (the default), the device must be
#      one LanWatch knows and is marked as recognised. An unrecognised device
#      doesn't even get the sign-in page: it is added to Devices at once and
#      waits for "Allow" in Settings > Phones and tablets (GROUP 1b).
#   3. it must be signed in with the admin username and password. Signing in
#      gives a random token kept in a cookie; only its one-way hash is stored
#      (PhoneSession table). It stays valid until 30 days without use, until
#      "Sign out" on the phone, or until it is signed out from Settings.
#
# The first-run set-up page can never be used from another device.
# Wrong passwords count towards the same pauses as the Settings lock (app/auth.py).
#
# Flow:  every request -> phone_gate() (registered in app/__init__.py)
#        sign-in page /phone-login -> POST /api/phone/login -> sign_in()
#        Settings "Signed-in phones and tablets" -> list_sessions() / sign_out_session()
# =============================================================================

import hashlib
import platform
import re
import secrets
import subprocess
import threading
import time

from flask import g, jsonify, redirect, render_template, request

from app.init_database import get_connection

COOKIE = "lanwatch_phone"
SESSION_DAYS = 30
LOCAL_ADDRESSES = ("127.0.0.1", "::1")

# paths a phone may open before signing in (the sign-in page and its styles)
OPEN_PATHS = ("/phone-login", "/api/phone/login", "/favicon.ico")
OPEN_PREFIXES = ("/static/",)
NEVER_FROM_PHONE = ("/setup",)                    # first-run set-up page


# =============================================================================
# GROUP 1: WHO IS ASKING?
# =============================================================================

def is_remote():
    """True for a request from another device (not this computer)."""
    return (request.remote_addr or "") not in LOCAL_ADDRESSES


def _setting(key):
    from app.routes.settings import get_bool_setting
    return get_bool_setting(key)


def mac_for_address(ip):
    """This device's network ID (MAC), e.g. "AA:BB:CC:DD:EE:FF", from Windows'
    neighbour (ARP) table, or None. A device that has just connected is
    always in that table, so this works even before any scan has found it."""
    if platform.system() != "Windows" or not ip:
        return None
    try:
        result = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == ip:
            mac = parts[1].replace("-", ":").upper()
            if re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac) and mac != "FF:FF:FF:FF:FF:FF":
                return mac
    return None


def device_for_address(ip, mac=None):
    """The Devices row for this device, or None. Matched by network ID first
    (phones often get a new IP address), then by IP address."""
    connection = get_connection()
    try:
        columns = "device_id, COALESCE(custom_name, hostname) AS name, recognised, device_ip"
        if mac:
            row = connection.execute(f"SELECT {columns} FROM Devices WHERE device_mac = ?", (mac,)).fetchone()
            if row:
                return row
        return connection.execute(f"""
            SELECT {columns} FROM Devices WHERE device_ip = ?
            ORDER BY last_detected DESC LIMIT 1""", (ip,)).fetchone()
    finally:
        connection.close()


# =============================================================================
# GROUP 1b: A DEVICE ASKING TO BE LET IN
# -----------------------------------------------------------------------------
# When "recognised devices only" is on and an unrecognised phone opens
# LanWatch, it is added to Devices straight away (no need to wait for a scan),
# a "new device" alert is raised if LanWatch had never seen it, and it is
# listed as WAITING in Settings > Phones and tablets, where the owner can
# click "Allow" (= "I know this device"). The phone's page checks again every
# few seconds and moves on to the sign-in page once it's allowed.
# Requests are kept in memory for WAITING_MINUTES (only while LanWatch runs).
# =============================================================================

WAITING_MINUTES = 15
_waiting = {}                          # device_id -> {device_id, name, ip, mac, label, at}
_waiting_lock = threading.Lock()


def _register_device(ip, mac):
    """Add a device LanWatch has never seen to Devices (not recognised) and
    raise the usual "new device" alert. Returns its Devices row."""
    from app.services.change_detection import _raise_alert
    label = _device_label(request.headers.get("User-Agent"))
    name = f"{label.split(' · ')[0]} ({ip})"
    try:
        from app.services.net_scanner import _lookup_vendor
        vendor = _lookup_vendor(mac) if mac else None
    except Exception:
        vendor = None
    connection = get_connection()
    try:
        cursor = connection.execute("""
            INSERT INTO Devices (hostname, name_source, device_ip, device_mac, device_mac_vendor,
                                 os_name, status, first_detected, last_detected, connected_since)
            VALUES (?, 'fallback', ?, ?, ?, 'Unknown', 'active',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (name, ip, mac, vendor))
        device_id = cursor.lastrowid
        maker = f" made by {vendor}" if vendor else ""
        _raise_alert(connection, device_id, "new_device", "medium",
                     f"New device asked to open LanWatch from your home network: {name} "
                     f"({ip}{maker}, network ID {mac or 'unknown'}).")
        connection.commit()
    finally:
        connection.close()
    print(f"[phone access] new device {ip} ({mac}) asked to open LanWatch", flush=True)
    return device_for_address(ip, mac)


def _note_waiting(device, ip, mac):
    """Remember that this unrecognised device asked to be let in."""
    with _waiting_lock:
        _waiting[device["device_id"]] = {
            "device_id": device["device_id"], "name": device["name"], "ip": ip, "mac": mac,
            "label": _device_label(request.headers.get("User-Agent")), "at": time.time()}


def waiting_devices():
    """Unrecognised devices that asked to be let in recently (newest first)."""
    cutoff = time.time() - WAITING_MINUTES * 60
    with _waiting_lock:
        for device_id in [d for d, w in _waiting.items() if w["at"] < cutoff]:
            del _waiting[device_id]
        items = list(_waiting.values())
    still_waiting = []
    connection = get_connection()
    try:
        for item in items:
            row = connection.execute("SELECT recognised FROM Devices WHERE device_id = ?",
                                     (item["device_id"],)).fetchone()
            if row and not row["recognised"]:
                still_waiting.append(dict(item, seconds_ago=int(time.time() - item["at"])))
    finally:
        connection.close()
    return sorted(still_waiting, key=lambda w: w["seconds_ago"])


def allow_device(device_id):
    """"Allow" in Settings: the same as "I know this device" on the Devices page."""
    connection = get_connection()
    try:
        cursor = connection.execute("""
            UPDATE Devices SET recognised = 1, recognised_by = 'you', recognised_at = CURRENT_TIMESTAMP
            WHERE device_id = ?""", (device_id,))
        connection.execute("""
            UPDATE Alert SET is_resolved = 1, resolved_at = CURRENT_TIMESTAMP
            WHERE device_id = ? AND alert_type = 'new_device' AND is_resolved = 0""", (device_id,))
        connection.commit()
    finally:
        connection.close()
    with _waiting_lock:
        _waiting.pop(device_id, None)
    return cursor.rowcount > 0


# =============================================================================
# GROUP 2: SIGN-IN SESSIONS (PhoneSession table)
# =============================================================================

def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _device_label(user_agent):
    """A short, readable name for the phone from its browser's description."""
    ua = user_agent or ""
    for needle, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android device"),
                          ("Windows", "Windows computer"), ("Macintosh", "Mac"), ("Linux", "Linux device")):
        if needle in ua:
            device = label
            break
    else:
        device = "Device"
    for needle, label in (("Edg/", "Edge"), ("SamsungBrowser", "Samsung Internet"), ("Firefox", "Firefox"),
                          ("CriOS", "Chrome"), ("Chrome", "Chrome"), ("Safari", "Safari")):
        if needle in ua:
            return f"{device} · {label}"
    return device


def current_session():
    """This request's valid phone session row, or None. Pushes 'last used' forward."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    connection = get_connection()
    try:
        row = connection.execute(f"""
            SELECT session_id, device_label, ip_address, created_at, last_used FROM PhoneSession
            WHERE token_hash = ? AND last_used >= datetime('now', '-{SESSION_DAYS} days')""",
            (_hash(token),)).fetchone()
        if row:
            connection.execute("UPDATE PhoneSession SET last_used = CURRENT_TIMESTAMP, ip_address = ? "
                               "WHERE session_id = ?", (request.remote_addr, row["session_id"]))
            connection.commit()
        return row
    finally:
        connection.close()


def sign_in(username, password):
    """Check the admin username + password; returns (token or None, message, wait_seconds)."""
    from app.auth import check_admin_password
    account, message, wait = check_admin_password(password, request.remote_addr or "unknown", username=username)
    if account is None:
        return None, message, wait
    token = secrets.token_urlsafe(32)
    connection = get_connection()
    try:
        # tidy up: forget sessions unused for too long
        connection.execute(f"DELETE FROM PhoneSession WHERE last_used < datetime('now', '-{SESSION_DAYS} days')")
        connection.execute("INSERT INTO PhoneSession (token_hash, device_label, ip_address) VALUES (?, ?, ?)",
                           (_hash(token), _device_label(request.headers.get("User-Agent")), request.remote_addr))
        connection.commit()
    finally:
        connection.close()
    return token, "", 0


def set_cookie(response, token):
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="Strict")
    return response


def sign_out_this_device(response):
    token = request.cookies.get(COOKIE)
    if token:
        connection = get_connection()
        try:
            connection.execute("DELETE FROM PhoneSession WHERE token_hash = ?", (_hash(token),))
            connection.commit()
        finally:
            connection.close()
    response.delete_cookie(COOKIE)
    return response


def list_sessions():
    connection = get_connection()
    try:
        rows = connection.execute(f"""
            SELECT session_id, device_label, ip_address, created_at, last_used FROM PhoneSession
            WHERE last_used >= datetime('now', '-{SESSION_DAYS} days') ORDER BY last_used DESC""").fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


def sign_out_session(session_id=None):
    """Sign out one phone (by id), or every phone (None). Returns how many."""
    connection = get_connection()
    try:
        if session_id is None:
            cursor = connection.execute("DELETE FROM PhoneSession")
        else:
            cursor = connection.execute("DELETE FROM PhoneSession WHERE session_id = ?", (session_id,))
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


# =============================================================================
# GROUP 3: THE GATE (runs before every request)
# =============================================================================

def _refuse(reason, status, **details):
    """A friendly page for browsers, or JSON for the pages' API calls."""
    if request.path.startswith("/api/"):
        messages = {"off": "Phone access is switched off.",
                    "unrecognised": "This device isn't recognised by LanWatch.",
                    "setup": "The admin account can only be created on the computer LanWatch runs on.",
                    "signin": "Sign in first."}
        return jsonify(error=messages[reason], phone_access=reason), status
    return render_template("phone_blocked.html", reason=reason, ip=request.remote_addr, **details), status


def phone_gate():
    """Flask before_request: let this computer through; check every other device."""
    g.phone_session = None
    if not is_remote():
        return None
    path = request.path

    if not _setting("phone_access_enabled"):
        return _refuse("off", 403)
    if path in NEVER_FROM_PHONE or (path == "/api/account/" and request.method == "POST"):
        return _refuse("setup", 403)              # creating the admin account
    if path.startswith(OPEN_PREFIXES) or path == "/favicon.ico":
        return None                               # styles, scripts, fonts, icons
    if _setting("phone_access_recognised_only"):
        ip = request.remote_addr
        device = device_for_address(ip)
        if device is None or not device["recognised"]:
            # not (yet) recognised under this address: look again by network ID,
            # which also finds a known phone that has a new IP address
            mac = mac_for_address(ip)
            device = device_for_address(ip, mac)
            if device is None:
                device = _register_device(ip, mac)
            elif device["recognised"] and device["device_ip"] != ip:
                _update_address(device["device_id"], ip)
            if device is None or not device["recognised"]:
                if device is not None:
                    _note_waiting(device, ip, mac)
                return _refuse("unrecognised", 403, name=device["name"] if device else None)
    if path in OPEN_PATHS:
        return None

    g.phone_session = current_session()
    if g.phone_session is None:
        if path.startswith("/api/"):
            return _refuse("signin", 401)
        from urllib.parse import quote
        return redirect("/phone-login?next=" + quote(safe_next(request.full_path), safe=""))
    return None


def safe_next(path):
    """Only ever send people back to a page of LanWatch itself."""
    path = (path or "/").rstrip("?")
    return path if path.startswith("/") and not path.startswith(("//", "/\\")) else "/"


def _update_address(device_id, ip):
    """A recognised device came back with a new IP address: note it."""
    connection = get_connection()
    try:
        connection.execute("UPDATE Devices SET device_ip = ?, last_detected = CURRENT_TIMESTAMP "
                           "WHERE device_id = ?", (ip, device_id))
        connection.commit()
    finally:
        connection.close()
