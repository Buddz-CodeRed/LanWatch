# =============================================================================
# FILE: app/services/change_detection.py  -  NEW DEVICES AND NEW OPEN PORTS
# -----------------------------------------------------------------------------
# After every scan, compares what was found with what LanWatch already knows
# ("the baseline") and raises an alert when something new appears.
#
# NEW DEVICE (every scan)
#   - Devices are told apart by their MAC address (their network ID), so a
#     device that gets a new IP address is still the same device.
#   - LEARNING PERIOD: for LEARNING_HOURS after LanWatch's very first scan,
#     everything found is treated as the household's own and recognised
#     automatically (otherwise a fresh install would alert on every device).
#   - After that, a device LanWatch has never seen before is NEW: it gets a
#     "NEW" badge on the pages and a "New device joined your network" alert.
#   - A new device becomes RECOGNISED when
#       * the user clicks "I know this device" (Devices page), or
#       * it has been seen on AUTO_RECOGNISE_SEEN_DAYS different days over at
#         least AUTO_RECOGNISE_DAYS days (it clearly belongs to the household).
#     The user can also mark it as unknown again.
#
# NEW OPEN PORT (full scans only: quick scans don't check every port)
#   - A port is NEW when it is open now but wasn't in any earlier full scan of
#     that device. A device's first full scan sets its normal ports.
#   - Ports of a device that is itself still NEW aren't reported separately
#     (the "new device" alert already covers it).
#   - It raises an alert when the knowledge base rates it medium or high risk,
#     or doesn't know the port at all; normal low-risk ports are only marked
#     "New" on the Devices page.
#
# Flow:  routes/scans.py _execute_scan() -> _save_scan_results()
#        -> detect_changes(scan_id, scan_type)
#           -> _baseline_start()            when the learning period began
#           -> _new_devices()               recognise / alert / auto-recognise
#           -> _new_ports()                 (full scans) alert on risky new ports
#        -> alerts appear on the Security Alerts page and the dashboard
# =============================================================================

from datetime import datetime, timedelta

from app.init_database import get_connection

LEARNING_HOURS = 24              # after the first scan, everything is "ours" for this long
AUTO_RECOGNISE_DAYS = 7          # a new device is recognised automatically after this many days...
AUTO_RECOGNISE_SEEN_DAYS = 3     # ...if it was seen on at least this many different days
DB_TIME = "%Y-%m-%d %H:%M:%S"    # how SQLite's CURRENT_TIMESTAMP writes times (UTC)


# =============================================================================
# GROUP 1: THE LEARNING PERIOD
# =============================================================================

def _baseline_start(connection):
    """When the learning period began (the first scan), saved in System_Config."""
    row = connection.execute(
        "SELECT value FROM System_Config WHERE key = 'baseline_started_at'").fetchone()
    if row:
        return datetime.strptime(row["value"], DB_TIME)
    now = datetime.utcnow().replace(microsecond=0)
    connection.execute("INSERT INTO System_Config (key, value) VALUES ('baseline_started_at', ?)",
                       (now.strftime(DB_TIME),))
    return now


def learning_status():
    """{learning: bool, ends_at: "YYYY-MM-DD HH:MM:SS" (UTC) or None} for the pages."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT value FROM System_Config WHERE key = 'baseline_started_at'").fetchone()
    finally:
        connection.close()
    if not row:
        return {"learning": True, "ends_at": None}            # no scan yet
    ends = datetime.strptime(row["value"], DB_TIME) + timedelta(hours=LEARNING_HOURS)
    return {"learning": datetime.utcnow() < ends, "ends_at": ends.strftime(DB_TIME)}


# =============================================================================
# GROUP 2: RUNNING THE CHECKS AFTER A SCAN
# =============================================================================

def detect_changes(scan_id, scan_type):
    """Check scan `scan_id` for new devices (and, for full scans, new open ports).
    Returns {"new_devices": n, "new_ports": n, "recognised": n}."""
    counts = {"new_devices": 0, "new_ports": 0, "recognised": 0}
    connection = get_connection()
    try:
        scan = connection.execute("SELECT start_time FROM Scans WHERE scan_id = ?", (scan_id,)).fetchone()
        if scan is None:
            return counts
        learning_ends = _baseline_start(connection) + timedelta(hours=LEARNING_HOURS)
        in_learning = datetime.utcnow() < learning_ends

        _new_devices(connection, scan_id, scan["start_time"], in_learning, counts)
        if scan_type == "full":
            _new_ports(connection, scan_id, counts)
        connection.commit()
    finally:
        connection.close()
    return counts


def _new_devices(connection, scan_id, scan_start, in_learning, counts):
    """Recognise devices (learning period / automatic) and alert on new ones."""
    rows = connection.execute("""
        SELECT d.device_id, COALESCE(d.custom_name, d.hostname) AS name, d.device_ip,
               d.device_mac, d.device_mac_vendor, d.first_detected
        FROM ScanMap sm JOIN Devices d ON d.device_id = sm.device_id
        WHERE sm.scan_id = ? AND d.recognised = 0""", (scan_id,)).fetchall()

    for row in rows:
        if in_learning:
            _recognise(connection, row["device_id"], "learning")
            counts["recognised"] += 1
            continue

        # first seen by THIS scan: a brand-new device
        if row["first_detected"] >= scan_start:
            maker = f" made by {row['device_mac_vendor']}" if row["device_mac_vendor"] else ""
            _raise_alert(connection, row["device_id"], "new_device", "medium",
                         f"New device joined your network: {row['name']} ({row['device_ip']}{maker}, "
                         f"network ID {row['device_mac'] or 'unknown'}).")
            counts["new_devices"] += 1
            continue

        # seen often enough over a week: it clearly belongs here
        first = datetime.strptime(row["first_detected"], DB_TIME)
        if datetime.utcnow() - first >= timedelta(days=AUTO_RECOGNISE_DAYS):
            days_seen = connection.execute("""
                SELECT COUNT(DISTINCT date(s.start_time))
                FROM ScanMap sm JOIN Scans s ON s.scan_id = sm.scan_id
                WHERE sm.device_id = ?""", (row["device_id"],)).fetchone()[0]
            if days_seen >= AUTO_RECOGNISE_SEEN_DAYS:
                _recognise(connection, row["device_id"], "automatic")
                counts["recognised"] += 1


def _new_ports(connection, scan_id, counts):
    """Alert on risky ports that are open now but weren't in earlier full scans."""
    from app.services.knowledge import explain_port

    rows = connection.execute("""
        SELECT dp.device_id, dp.port, dp.protocol, dp.service,
               COALESCE(d.custom_name, d.hostname) AS name, d.device_ip, d.recognised
        FROM DevicePorts dp JOIN Devices d ON d.device_id = dp.device_id
        WHERE dp.scan_id = ?
          -- the device had an earlier full scan (so it has "normal" ports to compare with)
          AND EXISTS (SELECT 1 FROM ScanMap sm JOIN Scans s ON s.scan_id = sm.scan_id
                      WHERE sm.device_id = dp.device_id AND s.scan_type = 'full'
                        AND s.scan_status = 'completed' AND s.scan_id < dp.scan_id)
          -- ...and this port wasn't open in any of them
          AND NOT EXISTS (SELECT 1 FROM DevicePorts old
                          WHERE old.device_id = dp.device_id AND old.port = dp.port
                            AND old.protocol = dp.protocol AND old.scan_id < dp.scan_id)""",
        (scan_id,)).fetchall()

    for row in rows:
        if not row["recognised"]:
            continue                                   # covered by the "new device" alert
        kb = explain_port(row["port"], row["protocol"], row["service"])
        if kb["known"] and kb["risk"] in ("info", "low"):
            continue                                   # normal: shown as "New" on the Devices page only
        severity = "high" if kb["risk"] == "high" else "medium"
        what = kb["name"] if kb["known"] else f"an unknown service ({row['service'] or 'unidentified'})"
        _raise_alert(connection, row["device_id"], "new_open_port", severity,
                     f"New open port {row['port']}/{row['protocol']} on {row['name']} ({row['device_ip']}): {what}.")
        counts["new_ports"] += 1


# =============================================================================
# GROUP 3: SMALL HELPERS
# =============================================================================

def _recognise(connection, device_id, how):
    connection.execute("""
        UPDATE Devices SET recognised = 1, recognised_by = ?, recognised_at = CURRENT_TIMESTAMP
        WHERE device_id = ?""", (how, device_id))


def _raise_alert(connection, device_id, alert_type, severity, message):
    """Save an alert unless the same one is already waiting (unresolved)."""
    if connection.execute("""
            SELECT 1 FROM Alert
            WHERE device_id = ? AND alert_type = ? AND message = ? AND is_resolved = 0""",
            (device_id, alert_type, message)).fetchone():
        return
    connection.execute("INSERT INTO Alert (device_id, alert_type, severity, message) VALUES (?, ?, ?, ?)",
                       (device_id, alert_type, severity, message))
