# =============================================================================
# FILE: app/routes/alerts.py  -  ALERTS API  (/api/alerts)
# -----------------------------------------------------------------------------
# Lists unresolved traffic-spike alerts and lets the user resolve them.
# Alerts are CREATED by services/network_monitor.py, not here.
#
# Flow (viewing):   dashboard.js loadAlerts() / alerts.js refresh() /
#                   devices.js refresh() / reports.js loadData()
#                   -> GET /api/alerts/ -> get_alerts() -> alert list/table
# Flow (resolving): user clicks "Mark as resolved" (alerts.js)
#                   -> POST /api/alerts/<id>/resolve -> resolve_alert()
#                   (admin password needed: if locked, admin_lock.js asks for it)
#                   -> alerts.js removes the card
# =============================================================================

from flask import Blueprint, jsonify

from app.auth import settings_unlock_required
from app.init_database import get_connection

alerts = Blueprint("alerts", __name__, url_prefix="/api/alerts")


# =============================================================================
# GROUP 1: READING ALERTS
# =============================================================================

@alerts.route("/", methods=["GET"])
def get_alerts():
    """GET /api/alerts/ - unresolved alerts, newest first, with device details."""
    connection = get_connection()
    rows = connection.execute("""
        SELECT a.alert_id, a.device_id, COALESCE(d.custom_name, d.hostname) AS hostname, d.device_ip, d.device_mac,
               a.alert_type, a.severity, a.message, a.created_at, a.is_resolved
        FROM Alert a
        LEFT JOIN Devices d ON a.device_id = d.device_id
        WHERE a.is_resolved = 0
        ORDER BY a.alert_id DESC""").fetchall()
    connection.close()
    return jsonify([dict(row) for row in rows])


# =============================================================================
# GROUP 2: RESOLVING AN ALERT
# =============================================================================

@alerts.route("/<int:alert_id>/resolve", methods=["POST"])
@settings_unlock_required
def resolve_alert(alert_id):
    """POST /api/alerts/<id>/resolve - mark one alert resolved.

    Needs the admin password (app/auth.py), like the Settings page:
    answers 401 {"locked": true} until unlocked.

    The row is kept (with the time it was resolved) so there is a history.
    Answers 404 if the alert doesn't exist or was already resolved.
    """
    connection = get_connection()
    cursor = connection.execute("""
        UPDATE Alert
        SET is_resolved = 1, resolved_at = CURRENT_TIMESTAMP
        WHERE alert_id = ? AND is_resolved = 0""", (alert_id,))
    changed = cursor.rowcount
    connection.commit()
    connection.close()

    if changed == 0:
        return jsonify({"error": "Alert not found or already resolved"}), 404
    return jsonify({"resolved": alert_id})
