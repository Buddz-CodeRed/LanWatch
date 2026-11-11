# =============================================================================
# FILE: app/routes/scans.py  -  SCANS API  (/api/scans)
# -----------------------------------------------------------------------------
# Starts network scans, reports their progress, and lists scan history.
# A scan never runs inside the web request: it runs in a background thread
# and the browser checks on it every 2 seconds.
#
# Two scan types (profiles in services/net_scanner.py SCAN_PROFILES):
#   quick - finds devices (a minute or two)
#   full  - finds devices AND their open ports/services, which are saved
#           in the DevicePorts table (can take 10-30 minutes)
#
# Flow (manual scan):
#   user picks Quick/Full, clicks "Run Scan" -> dashboard.js runScan()
#   -> POST /api/scans/ {"network", "scan_type"} -> run_scan() -> start_scan()  [GROUP 2]
#        creates the Scans row ("running") and starts _execute_scan()
#   -> answer 202 + scan_id straight away
#   -> background: _execute_scan() -> net_scanner.discover_devices()
#        -> _save_scan_results() (+ _save_open_ports() for full scans)
#        -> _check_for_changes() (services/change_detection.py: new devices, new ports)
#        -> full scans: _update_advice() (services/findings.py security advice)
#        -> _finish_scan()                                     [GROUP 3]
#        -> full scans: services/scan_summary.py writes the Dashboard summary
#   -> meanwhile dashboard.js pollScanStatus() calls
#      GET /api/scans/<id> -> get_scan() every 2 s             [GROUP 1]
#   -> when "completed": dashboard reloads devices + history tables
#
# Flow (returning to the dashboard mid-scan):
#   dashboard opens -> dashboard.js resumeActiveScan()
#   -> GET /api/scans/active -> get_active_scan()               [GROUP 1]
#   -> if a scan is running, the page carries on polling it
#
# Flow (automatic scan): services/scan_scheduler.py -> start_scan(network, "quick"/"full")
#                        -> same as above
# =============================================================================

import threading
import time

from flask import Blueprint, jsonify, request

from app.init_database import get_connection
from app.services.net_scanner import NAME_RANK, SCAN_TYPES, discover_devices, is_fallback_name, validate_network_target

scans = Blueprint("scans", __name__, url_prefix="/api/scans")

# stops two clicks at the same moment both starting a scan
_scan_start_lock = threading.Lock()

# scan_id -> the background thread running it. Lets the server tell a scan
# that is really still working from a "running" row whose worker has died.
_scan_workers = {}

# columns returned for every scan
SCAN_COLUMNS = """scan_id, network_range, scan_type, scan_status, start_time, end_time,
                  duration_seconds, results_summary"""


# =============================================================================
# GROUP 1: READING SCANS
# Used by the dashboard, the Scan History page, the Reports page and the
# dashboard's progress polling.
# =============================================================================

@scans.route("/", methods=["GET"])
def get_scans():
    """GET /api/scans/ - every scan, newest first."""
    connection = get_connection()
    rows = connection.execute(
        f"SELECT {SCAN_COLUMNS} FROM Scans ORDER BY scan_id DESC").fetchall()
    connection.close()

    return jsonify([dict(row) for row in rows])


@scans.route("/active", methods=["GET"])
def get_active_scan():
    """GET /api/scans/active - the scan running right now, or {"scan": null}.

    Called by dashboard.js resumeActiveScan() every time the dashboard opens,
    so a scan started before the user left the page is picked up again.
    """
    connection = get_connection()
    _clear_stale_scans(connection)

    scan = connection.execute(f"""
        SELECT {SCAN_COLUMNS} FROM Scans
        WHERE scan_status IN ('pending', 'running')
        ORDER BY scan_id DESC LIMIT 1""").fetchone()
    connection.close()

    return jsonify({"scan": dict(scan) if scan else None})


@scans.route("/<int:scan_id>", methods=["GET"])
def get_scan(scan_id):
    """GET /api/scans/<id> - one scan plus how many devices it has found so far.

    Called every 2 seconds by dashboard.js pollScanStatus() while a scan runs.
    """
    connection = get_connection()

    scan = connection.execute(
        f"SELECT {SCAN_COLUMNS} FROM Scans WHERE scan_id = ?", (scan_id,)).fetchone()

    if scan is None:
        connection.close()
        return jsonify({"error": "Scan not found"}), 404

    devices_found = connection.execute(
        "SELECT COUNT(*) FROM ScanMap WHERE scan_id = ?", (scan_id,)).fetchone()[0]
    connection.close()

    scan_data = dict(scan)
    scan_data["devices_found"] = devices_found
    return jsonify(scan_data)


# =============================================================================
# GROUP 2: STARTING A SCAN
# =============================================================================

@scans.route("/", methods=["POST"])
def run_scan():
    """POST /api/scans/ - start a scan.
    Body: {"network": "192.168.1.0/24", "scan_type": "quick" or "full"}
    (scan_type is optional and defaults to "quick").

    Called by dashboard.js runScan() when the user clicks "Run Scan".
    """
    data = request.get_json(silent=True) or {}
    network = (data.get("network") or "").strip()
    scan_type = (data.get("scan_type") or "quick").strip().lower()

    body, status_code = start_scan(network, scan_type)
    return jsonify(body), status_code


def start_scan(network, scan_type="quick"):
    """Validate, create the Scans row and start the background worker.

    Shared by run_scan() (the dashboard button) and services/scan_scheduler.py.
    Returns (response_body, http_status_code):
      202 started | 400 bad network range | 409 a scan is already running
    """
    if not network:
        return {"error": "Network range is required"}, 400

    if scan_type not in SCAN_TYPES:
        return {"error": f"Scan type must be one of: {', '.join(SCAN_TYPES)}"}, 400

    # validate before starting so bad input fails in milliseconds
    try:
        validate_network_target(network)
    except ValueError as error:
        return {"error": str(error)}, 400

    start_time = time.time()

    # only one scan at a time (nmap and sqlite do not like racing)
    with _scan_start_lock:
        connection = get_connection()
        _clear_stale_scans(connection)

        running_scan = connection.execute("""
            SELECT scan_id FROM Scans
            WHERE scan_status IN ('pending', 'running')
            LIMIT 1""").fetchone()

        if running_scan:
            connection.close()
            return {
                "error": "A scan is already running, wait for it to finish",
                "scan_id": running_scan["scan_id"],
            }, 409

        # create the scan record straight away with status "running";
        # this row is what GET /api/scans/<id> reports while the scan runs
        cursor = connection.execute("""
            INSERT INTO Scans (scan_type, scan_status, network_range, start_time)
            VALUES (?, 'running', ?, CURRENT_TIMESTAMP)""",
            (scan_type, network))
        connection.commit()
        scan_id = cursor.lastrowid
        connection.close()

        # run nmap in the background so the web request returns immediately
        worker = threading.Thread(
            target=_execute_scan,
            args=(scan_id, network, start_time, scan_type),
            daemon=True,
            name=f"Mrk1-scan-{scan_id}",
        )
        _scan_workers[scan_id] = worker
        worker.start()

    # 202 = "accepted, still working" - the page polls GET /api/scans/<id>
    return {"message": "Scan started", "scan_id": scan_id,
            "scan_type": scan_type, "status": "running"}, 202


def _clear_stale_scans(connection):
    """Mark 'pending'/'running' rows as failed when no worker thread is
    actually running them (e.g. the thread crashed or the server reloaded).

    Without this, one dead scan would say "running" forever and block every
    new scan with a 409. Real, still-working scans are left alone.
    """
    rows = connection.execute(
        "SELECT scan_id FROM Scans WHERE scan_status IN ('pending', 'running')").fetchall()

    for row in rows:
        worker = _scan_workers.get(row["scan_id"])
        if worker is None or not worker.is_alive():
            connection.execute("""
                UPDATE Scans
                SET scan_status = 'failed',
                    end_time = CURRENT_TIMESTAMP,
                    results_summary = 'scan interrupted (its worker stopped unexpectedly)'
                WHERE scan_id = ? AND scan_status IN ('pending', 'running')""",
                (row["scan_id"],))
            _scan_workers.pop(row["scan_id"], None)

    connection.commit()


# =============================================================================
# GROUP 3: BACKGROUND WORKER (runs in its own thread)
# =============================================================================

def _execute_scan(scan_id, network, start_time, scan_type="quick"):
    """Run nmap, save what it found, and record the outcome on the Scans row."""
    try:
        devices = discover_devices(network, scan_type=scan_type)
        _save_scan_results(scan_id, devices, scan_type)
        _check_for_changes(scan_id, scan_type)

        # the summary always starts with "N devices discovered"
        # (the Scan History page reads the device count from it)
        summary = f"{len(devices)} devices discovered"
        new_findings = 0
        if scan_type == "full":
            port_total = sum(len(device.get("open_ports", [])) for device in devices)
            summary += f", {port_total} open ports"
            new_findings = _update_advice(scan_id)

        _finish_scan(scan_id, "completed", int(time.time() - start_time), summary)

        if scan_type == "full":
            # plain-English summary for the Dashboard (AI optional, runs in the background)
            from app.services.scan_summary import write_summary_in_background
            write_summary_in_background(scan_id, new_findings)

    except Exception as error:
        # any failure (nmap timeout, nmap error, database error) is recorded,
        # so the scan never stays stuck on "running"
        _finish_scan(scan_id, "failed", int(time.time() - start_time), str(error))

    finally:
        _scan_workers.pop(scan_id, None)   # this scan is no longer being worked on


def _save_scan_results(scan_id, devices, scan_type="quick"):
    """Add new devices, update known ones, link them to this scan, and mark
    devices that were active but weren't found this time as inactive.
    Full scans also save each device's open ports."""
    connection = get_connection()
    try:
        for device in devices:
            device_id = _upsert_device(connection, device)

            if scan_type == "full":
                _save_open_ports(connection, scan_id, device_id, device.get("open_ports", []))

            # link the device to this scan
            connection.execute("""
                INSERT INTO ScanMap (scan_id, device_id, device_status, detection_status)
                VALUES (?, ?, ?, 'detected')""",
                (scan_id, device_id, device.get("status")))

        # anything active that this scan did not find is no longer active
        connection.execute("""
            UPDATE Devices
            SET status = 'inactive'
            WHERE status = 'active'
              AND device_id NOT IN (SELECT device_id FROM ScanMap WHERE scan_id = ?)""",
            (scan_id,))

        connection.commit()
    finally:
        connection.close()


def _upsert_device(connection, device):
    """Update the device if we know it (matched by MAC, else IP), or add it.

    Returns the device_id. Matching by MAC means a device that changes IP
    address is still recognised as the same device.
    """
    if device.get("device_mac"):
        existing = connection.execute(
            "SELECT device_id, hostname, name_source FROM Devices WHERE device_mac = ?",
            (device.get("device_mac"),)).fetchone()
    else:
        existing = connection.execute(
            "SELECT device_id, hostname, name_source FROM Devices WHERE device_ip = ?",
            (device.get("device_ip"),)).fetchone()

    new_name = device.get("device_name") or device.get("device_ip")
    new_source = device.get("name_source") or "fallback"

    if existing:
        # the automatic name: only replaced by a name from an equal or better
        # source (Windows name > router DNS > mDNS > made-up fallback), so e.g.
        # a router's mDNS model number never replaces its Windows name, and a
        # real name is never replaced by a fallback. Older rows without a
        # source count as fallback if they look made-up, else as the best.
        # (A name the user typed lives in custom_name; scans never touch it.)
        if existing["name_source"]:
            old_rank = NAME_RANK.get(existing["name_source"], 0)
        else:
            old_rank = 0 if is_fallback_name(existing["hostname"]) else NAME_RANK["netbios"]
        if NAME_RANK.get(new_source, 0) >= old_rank or new_name == existing["hostname"]:
            # (the same name found again also records where it came from,
            #  which fills in the source for older rows that don't have one)
            name_to_save, source_to_save = new_name, new_source
        else:
            name_to_save, source_to_save = existing["hostname"], existing["name_source"]
        # known device: refresh its details; keep connected_since if it was
        # already online, otherwise it has just come back so reset it to now
        connection.execute("""
            UPDATE Devices
            SET hostname = ?, name_source = ?,
                device_ip = ?, device_mac_vendor = COALESCE(?, device_mac_vendor),
                os_name = ?, status = ?,
                last_detected = CURRENT_TIMESTAMP,
                connected_since = CASE
                    WHEN status = 'active' AND connected_since IS NOT NULL THEN connected_since
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE device_id = ?""",
            (name_to_save, source_to_save, device.get("device_ip"), device.get("device_mac_vendor"),
             device.get("os_name", "Unknown"), device.get("status"), existing["device_id"]))
        return existing["device_id"]

    # new device: add it
    cursor = connection.execute("""
        INSERT INTO Devices (
            hostname, name_source, device_ip, device_mac, device_mac_vendor,
            os_name, status, first_detected, last_detected, connected_since)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (new_name, new_source,
         device.get("device_ip"), device.get("device_mac"),
         device.get("device_mac_vendor"), device.get("os_name", "Unknown"),
         device.get("status")))
    return cursor.lastrowid


def _check_for_changes(scan_id, scan_type):
    """New devices and (full scans) new risky open ports -> alerts
    (services/change_detection.py). A problem here never fails the scan."""
    try:
        from app.services.change_detection import detect_changes
        detect_changes(scan_id, scan_type)
    except Exception as error:
        print(f"[Change detection]: could not check scan {scan_id}: {error}")


def _update_advice(scan_id):
    """Run the security advice rules on a finished full scan (services/findings.py).

    Returns how many findings are new or came back. A rules problem is logged
    but never fails the scan itself.
    """
    try:
        from app.services.findings import update_findings
        counts = update_findings(scan_id)
        return counts["opened"] + counts["reopened"]
    except Exception as error:
        print(f"[Findings]: could not update security advice for scan {scan_id}: {error}")
        return 0


def _save_open_ports(connection, scan_id, device_id, open_ports):
    """Record the open ports a full scan found on one device (table DevicePorts).

    Every full scan adds a fresh set of rows, so the device's CURRENT ports
    are the ones from its latest full scan, and older scans keep their history.
    """
    connection.executemany("""
        INSERT INTO DevicePorts (scan_id, device_id, port, protocol, service, product, version)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(scan_id, device_id, p["port"], p["protocol"],
          p.get("service"), p.get("product"), p.get("version")) for p in open_ports])


def _finish_scan(scan_id, scan_status, duration_seconds, results_summary):
    """Write the final status ("completed"/"failed") back onto the Scans row."""
    try:
        connection = get_connection()
        connection.execute("""
            UPDATE Scans
            SET scan_status = ?,
                end_time = CURRENT_TIMESTAMP,
                duration_seconds = ?,
                results_summary = ?
            WHERE scan_id = ?""",
            (scan_status, duration_seconds, results_summary, scan_id))
        connection.commit()
        connection.close()

    except Exception as error:
        # never let a bookkeeping failure crash the worker silently
        print(f"Failed to record outcome for scan {scan_id}: {error}")
