# =============================================================================
# FILE: app/services/scan_summary.py  -  NETWORK SUMMARY AFTER EACH FULL SCAN
# -----------------------------------------------------------------------------
# After a full scan, writes a short summary of what was found into the Report
# table, so the Dashboard can show "Latest network summary":
#   tech_summary           - the facts as plain text (always written)
#   ai_translation_summary - the local AI's plain-English version
#                            (only if the AI is on, installed and
#                             "automatic summaries" is on in Settings)
#
# Flow:  routes/scans.py _execute_scan() finishes a FULL scan
#        -> write_summary_in_background(scan_id, new_findings)
#        -> background thread: _collect_facts() -> _facts_as_text()
#           -> ai_translator.explain() (optional) -> INSERT INTO Report
#        -> Dashboard: dashboard.js loadSummary() -> GET /api/reports/latest-summary
# =============================================================================

import threading
import time

from app.init_database import get_connection

AI_RETRIES = 8              # if the AI is busy, try again this many times...
AI_RETRY_WAIT_SECONDS = 15  # ...this far apart (2 minutes in total)


# =============================================================================
# GROUP 1: GATHERING THE FACTS
# =============================================================================

def collect_scan_facts(scan_id, new_findings=0):
    """Everything worth saying about one finished scan, as a dict.
    Also used by routes/ai.py to explain a scan."""
    connection = get_connection()
    try:
        scan = connection.execute("""
            SELECT scan_id, scan_type, scan_status, network_range, start_time,
                   duration_seconds, results_summary
            FROM Scans WHERE scan_id = ?""", (scan_id,)).fetchone()
        if scan is None:
            return None

        devices = connection.execute("""
            SELECT COALESCE(d.custom_name, d.hostname) AS hostname, d.device_ip, d.device_mac_vendor, d.first_detected
            FROM ScanMap sm JOIN Devices d ON d.device_id = sm.device_id
            WHERE sm.scan_id = ?""", (scan_id,)).fetchall()

        # "new" = first ever seen by this scan
        new_devices = [_label(d) for d in devices
                       if d["first_detected"] and d["first_detected"] >= scan["start_time"]]

        open_ports = connection.execute(
            "SELECT COUNT(*) FROM DevicePorts WHERE scan_id = ?", (scan_id,)).fetchone()[0]

        findings = connection.execute("""
            SELECT title, severity FROM Finding WHERE status = 'open'
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                   WHEN 'medium' THEN 2 ELSE 3 END, finding_id""").fetchall()
    finally:
        connection.close()

    by_severity = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    return {
        "scan_type": scan["scan_type"],
        "scan_status": scan["scan_status"],
        "network": scan["network_range"],
        "took_seconds": scan["duration_seconds"],
        "devices_found": len(devices),
        "new_devices": new_devices,
        "open_ports_found": open_ports if scan["scan_type"] == "full" else "not checked (quick scan)",
        "security_advice_to_review": len(findings),
        # stated directly: the AI misread the per-severity counts in testing
        "most_serious_advice_level": findings[0]["severity"] if findings else "none",
        "security_advice_by_severity": by_severity,
        "new_security_advice_from_this_scan": new_findings,
        "most_important_advice": [f["title"] for f in findings[:3]],
    }


def _label(device):
    """"Living-room TV (192.168.1.23, Samsung)" - name plus whatever else is known."""
    name = device["hostname"] or device["device_ip"]
    extras = [device["device_ip"]] if name != device["device_ip"] else []
    if device["device_mac_vendor"]:
        extras.append(device["device_mac_vendor"])
    return f"{name} ({', '.join(extras)})" if extras else name


def _facts_as_text(facts):
    """The facts as one or two plain sentences (shown when the AI isn't used)."""
    text = (f"{facts['scan_type'].title()} scan of {facts['network']}: "
            f"{facts['devices_found']} device{'s' if facts['devices_found'] != 1 else ''} found")
    if facts["new_devices"]:
        text += f", {len(facts['new_devices'])} new ({', '.join(facts['new_devices'][:3])})"
    if isinstance(facts["open_ports_found"], int):
        text += f", {facts['open_ports_found']} open port{'s' if facts['open_ports_found'] != 1 else ''}"
    text += ". "
    count = facts["security_advice_to_review"]
    if count:
        text += f"Security advice: {count} recommendation{'s' if count != 1 else ''} to review"
        if facts["most_important_advice"]:
            text += f", most important: {facts['most_important_advice'][0]}"
        text += "."
    else:
        text += "No security advice to review."
    return text


# =============================================================================
# GROUP 2: WRITING THE SUMMARY
# =============================================================================

def write_summary(scan_id, new_findings=0):
    """Collect the facts, ask the AI (if allowed), and save one Report row."""
    from app.routes.settings import get_bool_setting
    from app.services import ai_translator

    facts = collect_scan_facts(scan_id, new_findings)
    if facts is None:
        return None

    ai_text = None
    if get_bool_setting("ai_enabled") and get_bool_setting("ai_auto_summary"):
        for _ in range(AI_RETRIES):
            answer = ai_translator.explain(
                "Write a short summary of this network check for a home user: what was found, "
                "anything new, and the most important security advice to look at. Two to four sentences.",
                facts, max_words=110)
            if answer["reason"] != "busy":
                ai_text = answer["text"] if answer["ok"] else None
                break
            time.sleep(AI_RETRY_WAIT_SECONDS)

    connection = get_connection()
    try:
        cursor = connection.execute(
            "INSERT INTO Report (scan_id, tech_summary, ai_translation_summary) VALUES (?, ?, ?)",
            (scan_id, _facts_as_text(facts), ai_text))
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def write_summary_in_background(scan_id, new_findings=0):
    """Run write_summary() in its own thread, so a slow AI never delays the scan."""
    def worker():
        try:
            write_summary(scan_id, new_findings)
        except Exception as error:
            print(f"[Scan Summary]: could not write the summary for scan {scan_id}: {error}")

    threading.Thread(target=worker, daemon=True, name=f"Mrk1-summary-{scan_id}").start()
