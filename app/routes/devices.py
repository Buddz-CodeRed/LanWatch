# =============================================================================
# FILE: app/routes/devices.py  -  DEVICES API  (/api/devices)
# -----------------------------------------------------------------------------
# Lists every known device with its traffic totals, current speed and the
# open ports found by its latest full scan, and lets a device be added by hand.
#
# Flow:  dashboard.js loadDevices() / devices.js refresh() / reports.js loadData()
#        -> GET /api/devices/ -> get_devices()
#        -> reads Devices + Traffic tables -> JSON list
#        -> pages draw the device table, carousel cards and device details
# =============================================================================

import sqlite3

from flask import Blueprint, request, jsonify

from app.auth import settings_unlock_required
from app.init_database import get_connection
from app.services.knowledge import explain_port

devices = Blueprint(
    "devices",                  # internal name Flask uses for this group
    __name__,                   # tells Flask which file it lives in
    url_prefix="/api/devices"   # every route in this file starts with this
)

ALLOWED_STATUSES = ("active", "inactive", "unknown")


# =============================================================================
# GROUP 1: READING DEVICES
# =============================================================================

@devices.route("/", methods=["GET"])
def get_devices():
    """GET /api/devices/ - every device, plus three calculated values:

    - total_bytes / total_packets: everything the device has ever sent+received
    - throughput_bytes: current speed (bytes per second) from its latest traffic
      reading, or empty if that reading is old (two sampling intervals + 10 s),
      so an offline device never shows a stale speed
    - open_ports / last_full_scan: from the device's latest completed FULL
      scan (see _latest_open_ports); null if no full scan has found it yet
    """
    connection = get_connection()
    rows = connection.execute("""
        SELECT
            d.device_id,
            COALESCE(d.custom_name, d.hostname) AS hostname,   -- the name pages show
            d.hostname AS auto_name, d.custom_name,              -- for the Rename box
            d.recognised, d.recognised_by, d.recognised_at,      -- new device or one of ours
            d.device_ip, d.device_mac,
            d.device_mac_vendor, d.status, d.os_name,
            d.first_detected, d.last_detected, d.connected_since,
            COALESCE(t.total_bytes, 0)   AS total_bytes,
            COALESCE(t.total_packets, 0) AS total_packets,
            CASE WHEN l.timestamp >= datetime('now',
                        '-' || (MAX(l.measurement_interval, 1) * 2 + 10) || ' seconds')
                 THEN (l.bytes_sent + l.bytes_received) * 1.0
                        / MAX(l.measurement_interval, 1)
            END AS throughput_bytes
        FROM Devices d
        LEFT JOIN (
            SELECT device_id,
                SUM(bytes_sent + bytes_received)     AS total_bytes,
                SUM(packets_sent + packets_received) AS total_packets
            FROM Traffic
            GROUP BY device_id
        ) t ON t.device_id = d.device_id
        LEFT JOIN Traffic l
            ON l.traffic_id = (
                SELECT MAX(traffic_id) FROM Traffic WHERE device_id = d.device_id)
        ORDER BY d.device_id""").fetchall()
    ports_by_device = _latest_open_ports(connection)
    connection.close()

    # sqlite3.Row objects can't be turned into JSON directly, so convert to dicts
    result = []
    for row in rows:
        device = dict(row)
        found = ports_by_device.get(device["device_id"])
        device["open_ports"] = found["ports"] if found else None
        device["last_full_scan"] = found["scanned_at"] if found else None
        result.append(device)
    return jsonify(result)


def _latest_open_ports(connection):
    """{device_id: {"scanned_at": ..., "ports": [...]}} for every device that
    a completed full scan has found.

    Uses each device's LATEST full scan (through ScanMap), so a device whose
    newest full scan found no open ports correctly shows an empty list rather
    than old ports from an earlier scan.
    """
    rows = connection.execute("""
        WITH latest AS (
            SELECT sm.device_id, MAX(sm.scan_id) AS scan_id
            FROM ScanMap sm
            JOIN Scans s ON s.scan_id = sm.scan_id
            WHERE s.scan_type = 'full' AND s.scan_status = 'completed'
            GROUP BY sm.device_id
        )
        SELECT l.device_id, s.end_time AS scanned_at,
               dp.port, dp.protocol, dp.service, dp.product, dp.version,
               -- when this port was first seen open on this device
               (SELECT MIN(f.scan_id) FROM DevicePorts f
                 WHERE f.device_id = dp.device_id AND f.port = dp.port AND f.protocol = dp.protocol) AS first_scan,
               -- did the device have an earlier full scan to compare with?
               (SELECT COUNT(*) FROM ScanMap sm2 JOIN Scans s2 ON s2.scan_id = sm2.scan_id
                 WHERE sm2.device_id = l.device_id AND s2.scan_type = 'full'
                   AND s2.scan_status = 'completed' AND s2.scan_id < l.scan_id) AS earlier_full_scans,
               l.scan_id AS latest_scan
        FROM latest l
        JOIN Scans s ON s.scan_id = l.scan_id
        LEFT JOIN DevicePorts dp
               ON dp.scan_id = l.scan_id AND dp.device_id = l.device_id
        ORDER BY l.device_id, dp.protocol, dp.port""").fetchall()

    result = {}
    for row in rows:
        entry = result.setdefault(row["device_id"], {"scanned_at": row["scanned_at"], "ports": []})
        if row["port"] is not None:            # LEFT JOIN: no ports = one empty row
            # what the port is, how risky, why (hand-written: services/knowledge.py)
            kb = explain_port(row["port"], row["protocol"], row["service"])
            entry["ports"].append({
                "port": row["port"], "protocol": row["protocol"], "service": row["service"],
                "product": row["product"], "version": row["version"],
                "name": kb["name"], "risk": kb["risk"], "what": kb["what"], "why": kb["why"],
                # NEW = first opened in the latest full scan, on a device that had
                # earlier full scans (services/change_detection.py)
                "is_new": bool(row["earlier_full_scans"]) and row["first_scan"] == row["latest_scan"],
            })
    return result


# =============================================================================
# GROUP 2: ADDING A DEVICE BY HAND
# Not used by any page yet; available for a future "add device" form.
# =============================================================================

@devices.route("/", methods=["POST"])
def create_device():
    """POST /api/devices/ - add a device. Body needs at least {"device_ip": "..."}.

    Missing dates default to "now", status defaults to "active" and os_name to
    "Unknown". Answers 201 with the new device_id, 400 for bad input, or 409 if
    a device with that MAC address already exists.
    """
    data = request.get_json(silent=True) or {}

    device_ip = (data.get("device_ip") or "").strip()
    if not device_ip:
        return jsonify({"error": "device_ip is required"}), 400

    status = data.get("status") or "active"
    if status not in ALLOWED_STATUSES:
        return jsonify({"error": f"status must be one of {', '.join(ALLOWED_STATUSES)}"}), 400

    connection = get_connection()
    try:
        cursor = connection.execute("""
            INSERT INTO Devices (
                hostname, device_ip, device_mac, device_mac_vendor, os_name,
                status, first_detected, last_detected, connected_since)
            VALUES (?, ?, ?, ?, ?, ?,
                    COALESCE(?, CURRENT_TIMESTAMP),
                    COALESCE(?, CURRENT_TIMESTAMP),
                    COALESCE(?, CURRENT_TIMESTAMP))""",
            (
                data.get("hostname"),
                device_ip,
                data.get("device_mac"),
                data.get("device_mac_vendor"),
                data.get("os_name") or "Unknown",
                status,
                data.get("first_detected"),
                data.get("last_detected"),
                data.get("connected_since"),
            ))
        connection.commit()
        device_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"error": "A device with that MAC address already exists"}), 409
    finally:
        connection.close()

    # 201 = "created"
    return jsonify({"message": "Device created successfully", "device_id": device_id}), 201


# =============================================================================
# GROUP 3: RENAMING A DEVICE
# =============================================================================

MAX_NAME_LENGTH = 40


@devices.route("/<int:device_id>/name", methods=["PUT"])
def rename_device(device_id):
    """PUT /api/devices/<id>/name - body {"name": "Living-room TV"}.

    Saves the user's own name for the device (Devices.custom_name). Scans never
    change it. An empty name goes back to the automatic name.
    Called by devices.js saveName().
    """
    name = (request.get_json(silent=True) or {}).get("name")
    if name is not None and not isinstance(name, str):
        return jsonify({"error": "name must be text"}), 400
    name = " ".join((name or "").split())               # tidy spaces
    if len(name) > MAX_NAME_LENGTH:
        return jsonify({"error": f"Keep the name to {MAX_NAME_LENGTH} characters or fewer."}), 400

    connection = get_connection()
    try:
        cursor = connection.execute("UPDATE Devices SET custom_name = ? WHERE device_id = ?",
                                    (name or None, device_id))
        connection.commit()
        row = connection.execute(
            "SELECT COALESCE(custom_name, hostname) AS hostname, hostname AS auto_name, custom_name "
            "FROM Devices WHERE device_id = ?", (device_id,)).fetchone()
    finally:
        connection.close()
    if cursor.rowcount == 0:
        return jsonify({"error": "Device not found"}), 404
    return jsonify(dict(row))


# =============================================================================
# GROUP 4: NEW OR RECOGNISED
# =============================================================================

@devices.route("/<int:device_id>/recognised", methods=["PUT"])
@settings_unlock_required
def set_recognised(device_id):
    """PUT /api/devices/<id>/recognised - body {"recognised": true | false}.

    true  = "I know this device": recognised by you; its waiting
            "new device" alert is resolved at the same time.
    false = "Mark as unknown": it shows as NEW again.
    Needs the admin password (it's a trust decision, like resolving an alert).
    Called by devices.js setRecognised().
    """
    value = (request.get_json(silent=True) or {}).get("recognised")
    if not isinstance(value, bool):
        return jsonify({"error": "recognised must be true or false"}), 400

    connection = get_connection()
    try:
        if value:
            cursor = connection.execute("""
                UPDATE Devices SET recognised = 1, recognised_by = 'you', recognised_at = CURRENT_TIMESTAMP
                WHERE device_id = ?""", (device_id,))
            connection.execute("""
                UPDATE Alert SET is_resolved = 1, resolved_at = CURRENT_TIMESTAMP
                WHERE device_id = ? AND alert_type = 'new_device' AND is_resolved = 0""", (device_id,))
        else:
            cursor = connection.execute("""
                UPDATE Devices SET recognised = 0, recognised_by = NULL, recognised_at = NULL
                WHERE device_id = ?""", (device_id,))
        connection.commit()
    finally:
        connection.close()
    if cursor.rowcount == 0:
        return jsonify({"error": "Device not found"}), 404
    return jsonify({"device_id": device_id, "recognised": value})


@devices.route("/learning", methods=["GET"])
def learning():
    """GET /api/devices/learning - is LanWatch still in its first-day learning period?"""
    from app.services.change_detection import learning_status
    return jsonify(learning_status())

