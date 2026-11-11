# =============================================================================
# FILE: app/services/findings.py  -  SECURITY ADVICE RULES ("findings")
# -----------------------------------------------------------------------------
# Turns the results of a FULL scan into specific, advice-only recommendations,
# e.g. "Turn off Telnet on HP printer (192.168.1.141)".
#
# LanWatch NEVER changes anything on a device and NEVER tries passwords.
# A finding only points the user to an advice article (knowledge/remediation.json).
#
# Flow:  routes/scans.py _execute_scan() finishes a FULL scan
#        -> update_findings(scan_id)                        [GROUP 3]
#           -> for every device the scan found: _device_facts() + its open ports
#           -> each rule in RULES checks them                [GROUP 1]
#           -> matched: create the finding (or re-open it if it came back)
#           -> no longer matched: mark it "done" automatically (progress!)
#        -> Security Advice page: routes/findings.py GET /api/findings/
# =============================================================================

from app.init_database import get_connection


# =============================================================================
# GROUP 1: THE RULES
# Each rule: (rule_id, severity, article_id, check, detail)
#   check(device, ports)  -> True when the rule applies
#   detail(device, ports) -> one sentence saying what LanWatch saw
# `ports` is the set of open TCP port numbers from the latest full scan.
# Full scans check TCP ports, so UDP-only services (e.g. UPnP's port 1900)
# are recognised through their usual TCP ports instead.
# =============================================================================

DATABASE_PORTS = {3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis", 27017: "MongoDB"}
ROUTER_WEB_PORTS = {80, 443, 8080, 8443}


def _db_names(ports):
    return ", ".join(name for port, name in DATABASE_PORTS.items() if port in ports)


RULES = [
    ("telnet_open", "high", "disable_telnet",
     lambda d, p: 23 in p,
     lambda d, p: "Telnet (port 23) is open. It sends passwords without encryption."),

    ("vnc_open", "high", "review_remote_desktop",
     lambda d, p: bool({5900, 5901} & p),
     lambda d, p: "VNC remote screen control (port 5900) is open."),

    ("database_open", "medium", "protect_databases",
     lambda d, p: bool(set(DATABASE_PORTS) & p),
     lambda d, p: f"A database ({_db_names(p)}) accepts connections from the network."),

    ("ftp_open", "medium", "disable_ftp",
     lambda d, p: 21 in p,
     lambda d, p: "FTP file transfer (port 21) is open. It sends passwords and files without encryption."),

    ("rdp_open", "medium", "review_remote_desktop",
     lambda d, p: 3389 in p,
     lambda d, p: "Windows Remote Desktop (port 3389) is switched on."),

    ("mqtt_unencrypted", "medium", "secure_smart_home",
     lambda d, p: 1883 in p,
     lambda d, p: "Unencrypted smart-home messaging (MQTT, port 1883) is open."),

    ("camera_stream", "medium", "secure_cameras",
     lambda d, p: 554 in p,
     lambda d, p: "A video stream (RTSP, port 554) is available from this device."),

    ("smb_on_non_pc", "medium", "review_file_sharing",
     lambda d, p: 445 in p and not d["shares_files_normally"],
     lambda d, p: "File sharing (SMB, port 445) is on, which is unusual for this kind of device."),

    ("router_http_admin", "medium", "router_https_admin",
     lambda d, p: d["is_gateway"] and bool({80, 8080} & p) and not ({443, 8443} & p),
     lambda d, p: "Your router's settings page is only available without encryption (HTTP)."),

    ("router_default_password", "medium", "router_default_password",
     lambda d, p: d["is_gateway"] and bool(ROUTER_WEB_PORTS & p),
     lambda d, p: "Your router's settings page can be opened from your network. Check its admin password isn't the default."),

    ("router_upnp", "low", "disable_upnp",
     lambda d, p: d["is_gateway"] and bool({5000, 49152} & p),
     lambda d, p: "Your router answers on a port that UPnP (automatic port opening) usually uses, so UPnP may be switched on."),
]

# makers whose devices normally share files (network storage)
NAS_VENDORS = ("synology", "qnap", "western digital", "asustor", "buffalo", "terramaster", "drobo")


# =============================================================================
# GROUP 2: FACTS ABOUT ONE DEVICE
# =============================================================================

def _device_facts(row, gateways):
    """The extra facts the rules need: is it the router? does it normally share files?"""
    os_name = (row["os_name"] or "").lower()
    vendor = (row["device_mac_vendor"] or "").lower()
    return {
        "device_id": row["device_id"],
        "is_gateway": row["device_ip"] in gateways,
        # computers and network storage share files as normal; TVs, cameras etc. don't
        "shares_files_normally": (
            any(word in os_name for word in ("windows", "mac os", "macos"))
            or any(v in vendor for v in NAS_VENDORS)
        ),
    }


def _device_label(row):
    name = row["hostname"] or "Unknown device"
    return name if name == row["device_ip"] else f"{name} ({row['device_ip']})"


# =============================================================================
# GROUP 3: RUNNING THE RULES AFTER A FULL SCAN
# =============================================================================

def update_findings(scan_id, gateways=None):
    """Check every device found by full scan `scan_id` against RULES.

    Returns {"opened": n, "reopened": n, "closed": n}. Findings the user
    dismissed ("not relevant to me") stay dismissed.
    """
    if gateways is None:
        from app.services.net_scanner import default_gateways
        gateways = default_gateways()

    counts = {"opened": 0, "reopened": 0, "closed": 0}
    connection = get_connection()
    try:
        devices = connection.execute("""
            SELECT d.device_id, COALESCE(d.custom_name, d.hostname) AS hostname, d.device_ip, d.os_name, d.device_mac_vendor
            FROM ScanMap sm JOIN Devices d ON d.device_id = sm.device_id
            WHERE sm.scan_id = ?""", (scan_id,)).fetchall()

        for row in devices:
            ports = {r["port"] for r in connection.execute(
                "SELECT port FROM DevicePorts WHERE scan_id = ? AND device_id = ? AND protocol = 'tcp'",
                (scan_id, row["device_id"]))}
            facts = _device_facts(row, gateways)
            existing = {r["rule_id"]: r for r in connection.execute(
                "SELECT finding_id, rule_id, status FROM Finding WHERE device_id = ?", (row["device_id"],))}

            for rule_id, severity, article_id, check, detail in RULES:
                old = existing.get(rule_id)
                if check(facts, ports):
                    _record_match(connection, row, rule_id, severity, article_id,
                                  detail(facts, ports), old, counts)
                elif old is not None and old["status"] == "open":
                    # the problem has gone (e.g. Telnet switched off): mark it done
                    connection.execute(
                        "UPDATE Finding SET status = 'done', updated_at = CURRENT_TIMESTAMP WHERE finding_id = ?",
                        (old["finding_id"],))
                    counts["closed"] += 1

        connection.commit()
    finally:
        connection.close()
    return counts


def _record_match(connection, row, rule_id, severity, article_id, detail, old, counts):
    """Create the finding, refresh it, or re-open it if it had been fixed and came back."""
    from app.services.knowledge import get_article

    article = get_article(article_id) or {"title": rule_id}
    title = f"{article['title']}: {_device_label(row)}"

    if old is None:
        connection.execute("""
            INSERT INTO Finding (device_id, rule_id, severity, title, detail, article_id)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (row["device_id"], rule_id, severity, title, detail, article_id))
        counts["opened"] += 1
    elif old["status"] == "done":
        connection.execute("""
            UPDATE Finding SET status = 'open', severity = ?, title = ?, detail = ?,
                               updated_at = CURRENT_TIMESTAMP
            WHERE finding_id = ?""", (severity, title, detail, old["finding_id"]))
        counts["reopened"] += 1
    else:
        # still open, or dismissed by the user: keep the status, refresh the wording
        connection.execute(
            "UPDATE Finding SET severity = ?, title = ?, detail = ? WHERE finding_id = ?",
            (severity, title, detail, old["finding_id"]))
