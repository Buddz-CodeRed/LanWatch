# =============================================================================
# FILE: app/services/scan_scheduler.py  -  AUTOMATIC QUICK AND FULL SCANS
# -----------------------------------------------------------------------------
# Runs scans on its own, using two intervals from the System Settings page:
#   "Automatic quick scan" every N minutes  (quick_scan_interval_minutes, 0 = off)
#   "Automatic full scan"  every N minutes  (full_scan_interval_minutes,  0 = off)
#
# Only one scan runs at a time. When both are due, the FULL scan goes first:
# it finds everything a quick scan finds, so the quick scan isn't needed
# straight afterwards (a full scan also counts as a recent quick scan).
#
# Flow:  create_app() (app/__init__.py) -> start_scan_scheduler()
#        -> background thread wakes every CHECK_EVERY_SECONDS -> _run_due_scan()
#        -> _scan_is_due()? -> _pick_network() -> start_scan() in routes/scans.py
#        -> the scan runs exactly like one started from the dashboard button,
#           so it appears in the dashboard, the header indicator and Scan History.
# =============================================================================

import threading
import time

from app.init_database import get_connection

# how often the scheduler checks whether a scan is due
CHECK_EVERY_SECONDS = 30


# =============================================================================
# GROUP 1: DECIDING WHEN AND WHAT TO SCAN
# =============================================================================

def _scan_is_due(interval_minutes, scan_types):
    """True if no scan of the given type(s) has started in the last interval.

    scan_types is a tuple: ("full",) for full scans, ("quick", "full") for
    quick scans (any recent scan, quick or full, makes a quick scan unnecessary).
    """
    placeholders = ",".join("?" * len(scan_types))
    connection = get_connection()
    try:
        recent = connection.execute(
            f"""SELECT 1 FROM Scans
                WHERE scan_type IN ({placeholders})
                  AND start_time >= datetime('now', ?)
                LIMIT 1""",
            (*scan_types, f"-{int(interval_minutes)} minutes"),
        ).fetchone()
    finally:
        connection.close()
    return recent is None


def _pick_network():
    """The network to scan: the saved default, or the detected one (or None)."""
    from app.routes.settings import get_setting
    from app.services.net_scanner import detect_local_subnets

    default_subnet = get_setting("default_subnet") or ""
    if default_subnet:
        return default_subnet
    subnets = detect_local_subnets()
    return subnets[0] if subnets else None


def _which_scan_is_due():
    """"full", "quick" or None. Full scans are checked first (they have priority)."""
    from app.routes.settings import get_int_setting, SCAN_INTERVAL_MAX

    full_interval = get_int_setting("full_scan_interval_minutes", 0, 0, SCAN_INTERVAL_MAX)
    if full_interval and _scan_is_due(full_interval, ("full",)):
        return "full"

    quick_interval = get_int_setting("quick_scan_interval_minutes", 0, 0, SCAN_INTERVAL_MAX)
    if quick_interval and _scan_is_due(quick_interval, ("quick", "full")):
        return "quick"

    return None


# =============================================================================
# GROUP 2: THE SCHEDULER LOOP
# =============================================================================

def _run_due_scan():
    """One check: start whichever automatic scan is due (if any)."""
    from app.routes.scans import start_scan

    scan_type = _which_scan_is_due()
    if scan_type is None:
        return

    network = _pick_network()
    if not network:
        print("[Scan Scheduler]: no network to scan (no default set and none detected)")
        return

    body, status_code = start_scan(network, scan_type)
    # 409 = a scan is already running: simply try again at the next check
    if status_code not in (202, 409):
        print(f"[Scan Scheduler]: could not start {scan_type} scan: {body.get('error')}")


def start_scan_scheduler():
    """Start the background thread that runs automatic quick and full scans."""

    def scheduler_loop():
        while True:
            try:
                _run_due_scan()
            except Exception as err:
                # never let one bad check stop the scheduler
                print(f"[Scan Scheduler Error]: {err}")
            time.sleep(CHECK_EVERY_SECONDS)

    threading.Thread(target=scheduler_loop, daemon=True, name="ScanScheduler").start()
