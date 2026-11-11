# =============================================================================
# FILE: app/routes/findings.py  -  SECURITY ADVICE API  (/api/findings, /api/knowledge)
# -----------------------------------------------------------------------------
# Lists the security advice ("findings") raised after full scans, lets the user
# mark each one done / not relevant, and serves the port knowledge base.
# Findings are CREATED by services/findings.py, not here.
#
# Flow (viewing):  advice.js loadAdvice() / dashboard.js loadSecurityOverview()
#                  -> GET /api/findings/ -> get_findings()
#                  -> each finding comes with its advice article (steps to fix)
# Flow (updating): user clicks "I've done this" / "Not relevant to me" / "Undo"
#                  -> POST /api/findings/<id>/status -> set_finding_status()
# Flow (router):   advice.js loadRouter() -> GET/PUT /api/knowledge/router
#                  -> services/router_advice.py  [GROUP 4]
# =============================================================================

from flask import Blueprint, jsonify, request

from app.init_database import get_connection
from app.services.knowledge import explain_port, get_article

findings_api = Blueprint("findings_api", __name__, url_prefix="/api")

STATUSES = ("open", "done", "dismissed")
SEVERITY_ORDER = """CASE f.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                    WHEN 'medium' THEN 2 ELSE 3 END"""


# =============================================================================
# GROUP 1: READING FINDINGS
# =============================================================================

@findings_api.route("/findings/", methods=["GET"])
def get_findings():
    """GET /api/findings/ - every finding, open ones first (most serious first),
    each with its device and advice article, plus counts per status."""
    connection = get_connection()
    rows = connection.execute(f"""
        SELECT f.finding_id, f.device_id, f.rule_id, f.severity, f.title, f.detail,
               f.article_id, f.status, f.created_at, f.updated_at,
               COALESCE(d.custom_name, d.hostname) AS hostname, d.device_ip, d.device_mac_vendor, d.os_name
        FROM Finding f
        JOIN Devices d ON d.device_id = f.device_id
        ORDER BY CASE f.status WHEN 'open' THEN 0 ELSE 1 END, {SEVERITY_ORDER},
                 f.updated_at DESC""").fetchall()
    connection.close()

    findings = []
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        finding = dict(row)
        finding["article"] = get_article(row["article_id"]) if row["article_id"] else None
        findings.append(finding)
        counts[row["status"]] += 1

    return jsonify({"findings": findings, "counts": counts})


# =============================================================================
# GROUP 2: UPDATING A FINDING
# =============================================================================

@findings_api.route("/findings/<int:finding_id>/status", methods=["POST"])
def set_finding_status(finding_id):
    """POST /api/findings/<id>/status - body {"status": "done" | "dismissed" | "open"}.

    "done" = the user followed the advice (the next full scan checks it again),
    "dismissed" = not relevant to them (stays hidden even if seen again),
    "open" = undo.
    """
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in STATUSES:
        return jsonify({"error": f"status must be one of: {', '.join(STATUSES)}"}), 400

    connection = get_connection()
    cursor = connection.execute(
        "UPDATE Finding SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE finding_id = ?",
        (status, finding_id))
    connection.commit()
    connection.close()

    if cursor.rowcount == 0:
        return jsonify({"error": "Finding not found"}), 404
    return jsonify({"finding_id": finding_id, "status": status})


# =============================================================================
# GROUP 3: KNOWLEDGE BASE
# =============================================================================

@findings_api.route("/knowledge/ports/<int:port>", methods=["GET"])
def get_port_knowledge(port):
    """GET /api/knowledge/ports/<port>?protocol=tcp - what a port is and how risky."""
    protocol = request.args.get("protocol", "tcp")
    if protocol not in ("tcp", "udp") or not 1 <= port <= 65535:
        return jsonify({"error": "Use a port between 1 and 65535 and protocol tcp or udp"}), 400
    entry = explain_port(port, protocol)
    entry["advice"] = [get_article(a) for a in entry["advice"] if get_article(a)]
    return jsonify(entry)


# =============================================================================
# GROUP 4: YOUR ROUTER (hardening guide for the router + NZ internet providers)
# Knowledge: app/knowledge/routers.json + isps.json -> services/router_advice.py
# =============================================================================

@findings_api.route("/knowledge/router", methods=["GET"])
def get_router_guide():
    """GET /api/knowledge/router - the router LanWatch found, its brand / model /
    provider, and the hardening checklist with the user's ticks."""
    from app.services.router_advice import router_guide
    return jsonify(router_guide())


@findings_api.route("/knowledge/router", methods=["PUT"])
def save_router_guide():
    """PUT /api/knowledge/router - body {"isp": "spark" | "" (not sure)} and/or
    {"done": {"firmware": true}}. Only LanWatch's own notes change: the router never does."""
    from app.services.router_advice import router_guide, save_router_choices
    body = request.get_json(silent=True) or {}
    isp = body.get("isp")
    if isp is not None and not isinstance(isp, str):
        return jsonify({"error": "isp must be text"}), 400
    try:
        save_router_choices(isp=isp, done=body.get("done"))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400
    return jsonify(router_guide())
