# =============================================================================
# FILE: app/routes/usage.py  -  USAGE API  (/api/usage)
# -----------------------------------------------------------------------------
# Whole-network traffic numbers for the dashboard and the Reports page.
#
# Flow:  dashboard.js loadTotalTraffic() / reports.js loadData()
#        -> GET /api/usage/total -> get_total_usage()
#        -> reads the Traffic table (filled by services/network_monitor.py)
#        -> dashboard "Traffic Monitoring" card shows the live speed + Live/Idle
# =============================================================================

from flask import Blueprint, jsonify

from app.init_database import get_connection

usage = Blueprint("usage", __name__, url_prefix="/api/usage")


# =============================================================================
# GROUP 1: NETWORK TOTALS
# =============================================================================

@usage.route("/total", methods=["GET"])
def get_total_usage():
    """GET /api/usage/total - live speed and all-time totals for the network.

    live_*: bytes per second, from each device's latest reading (only readings
            newer than two sampling intervals + 10 s count, so idle = 0)
    grand_total_bytes: everything LanWatch has ever recorded
    """
    connection = get_connection()

    # 1. live speed: latest reading per device, converted to bytes per second
    live = connection.execute("""
        SELECT
            COALESCE(SUM(l.bytes_sent     * 1.0 / MAX(l.measurement_interval, 1)), 0) AS live_bytes_sent,
            COALESCE(SUM(l.bytes_received * 1.0 / MAX(l.measurement_interval, 1)), 0) AS live_bytes_received
        FROM Traffic l
        WHERE l.traffic_id IN (SELECT MAX(traffic_id) FROM Traffic GROUP BY device_id)
          AND l.timestamp >= datetime('now',
                '-' || (MAX(l.measurement_interval, 1) * 2 + 10) || ' seconds')
    """).fetchone()

    # 2. all-time totals across every device
    grand = connection.execute("""
        SELECT
            COALESCE(SUM(bytes_sent), 0)     AS total_bytes_sent,
            COALESCE(SUM(bytes_received), 0) AS total_bytes_received
        FROM Traffic
    """).fetchone()

    connection.close()

    live_sent = round(live["live_bytes_sent"])
    live_received = round(live["live_bytes_received"])

    return jsonify({
        "live_throughput_bytes": live_sent + live_received,
        "live_bytes_sent": live_sent,
        "live_bytes_received": live_received,
        "grand_total_bytes": grand["total_bytes_sent"] + grand["total_bytes_received"],
    })
