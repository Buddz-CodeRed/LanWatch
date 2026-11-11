# =============================================================================
# FILE: app/routes/ai.py  -  AI API  (/api/ai, /api/reports)
# -----------------------------------------------------------------------------
# Lets the pages ask the local AI (services/ai_translator.py) for plain-English
# explanations, and serves the after-scan network summary.
#
# Flow ("Explain this" button on any page):
#   ai_explain.js -> POST /api/ai/explain {"kind": "alert", "id": 12}
#   -> explain() -> _facts_for() looks the facts up in the DATABASE itself
#      (+ the hand-written meaning from services/knowledge.py)
#   -> ai_translator.explain(task, facts) -> {"text": "..."}
#   The page never sends the facts, so it can't feed the AI made-up data.
#
# Flow (Reports page "Generate"):
#   reports.js generateAiReport() -> POST /api/reports/generate -> generate_report()
#
# Flow (Dashboard "Latest network summary"):
#   dashboard.js loadSecurityOverview() -> GET /api/reports/latest-summary
#   (written after each full scan by services/scan_summary.py)
#
# Every AI answer is labelled on the page as an AI summary that may contain
# mistakes, with the original facts shown next to it.
# =============================================================================

from flask import Blueprint, jsonify, request

from app.init_database import get_connection
from app.routes.settings import get_bool_setting
from app.services import ai_translator
from app.services.knowledge import explain_alert_type, explain_port, get_article

ai_api = Blueprint("ai_api", __name__, url_prefix="/api")

KINDS = ("alert", "port", "finding", "scan")

# what each kind of explanation should say (the "task" given to the AI)
TASKS = {
    "alert": ("Explain this alert to a home user: what happened, how worried they should be, "
              "and what to check first."),
    "port": ("Explain to a home user what this open port on their device is, whether it is a "
             "concern for this kind of device, and what to do about it (if anything)."),
    "finding": ("Explain this security recommendation to a home user in your own words: what the "
                "problem is, why it matters, and the first step to fix it."),
    "scan": "Summarise this network scan for a home user in two to four sentences.",
}

REPORT_TASKS = {
    "overview": "Write a short overall summary of this home network for its owner.",
    "devices": "Write a short summary of the devices on this home network for its owner.",
    "security": "Write a short security summary of this home network for its owner, most important first.",
    "history": "Write a short summary of the scan history of this home network for its owner.",
}

# the answer to each reason explain() can give instead of text
REFUSALS = {
    "disabled": ("The AI assistant is switched off in System Settings.", 403),
    "unavailable": ("The AI model isn't installed, so AI explanations aren't available.", 503),
    "busy": ("The AI is busy with another question. Try again in a moment.", 503),
    "error": ("The AI couldn't answer this time. The explanation above is still accurate.", 500),
}


# =============================================================================
# GROUP 1: STATUS
# =============================================================================

@ai_api.route("/ai/status", methods=["GET"])
def ai_status():
    """GET /api/ai/status - can the pages offer AI buttons?
    {available, enabled, ready, busy, loaded}. ready = available AND enabled."""
    status = ai_translator.status()
    enabled = get_bool_setting("ai_enabled")
    return jsonify({
        "available": status["available"],
        "enabled": enabled,
        "ready": status["available"] and enabled,
        "busy": status["busy"],
        "loaded": status["loaded"],
        "last_error": status["last_error"],
    })


# =============================================================================
# GROUP 2: EXPLAIN ONE THING
# =============================================================================

@ai_api.route("/ai/explain", methods=["POST"])
def explain():
    """POST /api/ai/explain - body {"kind": "alert"|"port"|"finding"|"scan", "id": <id>}
    (for a port also "port": <number>, with "id" = the device id).

    Answers {"text": "...", "cached": bool}, or {"error": "..."} with
    400 (bad request), 404 (not found), 403/503/500 (AI not able to answer).
    """
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind not in KINDS:
        return jsonify({"error": f"kind must be one of: {', '.join(KINDS)}"}), 400
    try:
        item_id = int(body.get("id"))
        port = int(body["port"]) if kind == "port" else None
    except (TypeError, ValueError, KeyError):
        return jsonify({"error": "id (and port, for kind 'port') must be numbers"}), 400

    facts = _facts_for(kind, item_id, port)
    if facts is None:
        return jsonify({"error": f"That {kind} wasn't found."}), 404

    body, code = _ask(TASKS[kind], facts, max_words=150)
    return jsonify(body), code


def _ask(task, facts, max_words):
    """Ask the AI (if switched on). Returns (response_body, http_status_code):
    ({"text", "cached"}, 200) or ({"error", "reason"}, 403/503/500)."""
    if not get_bool_setting("ai_enabled"):
        message, code = REFUSALS["disabled"]
        return {"error": message, "reason": "disabled"}, code

    answer = ai_translator.explain(task, facts, max_words=max_words)
    if not answer["ok"]:
        message, code = REFUSALS[answer["reason"]]
        return {"error": message, "reason": answer["reason"]}, code
    return {"text": answer["text"], "cached": answer["cached"]}, 200


# =============================================================================
# GROUP 3: LOOKING UP THE FACTS (from the database + knowledge base)
# =============================================================================

def _device_facts(row):
    """The device details the AI may use."""
    return {
        "name": row["hostname"] or "unknown",
        "ip_address": row["device_ip"],
        "maker": row["device_mac_vendor"] or "unknown",
        "device_type_guess": row["os_name"] or "unknown",
        "online": row["status"] == "active",
    }


def _facts_for(kind, item_id, port=None):
    """Build the facts for one item, or None if it doesn't exist."""
    connection = get_connection()
    try:
        if kind == "alert":
            row = connection.execute("""
                SELECT a.alert_type, a.severity, a.message, a.created_at, a.is_resolved,
                       COALESCE(d.custom_name, d.hostname) AS hostname, d.device_ip, d.device_mac_vendor, d.os_name, d.status
                FROM Alert a LEFT JOIN Devices d ON d.device_id = a.device_id
                WHERE a.alert_id = ?""", (item_id,)).fetchone()
            if row is None:
                return None
            return {
                "alert": {"severity": row["severity"], "measured": row["message"],
                          "when_utc": row["created_at"], "resolved": bool(row["is_resolved"])},
                "what_this_kind_of_alert_means": explain_alert_type(row["alert_type"]),
                "device": _device_facts(row) if row["device_ip"] else "unknown device",
            }

        if kind == "port":
            device = connection.execute(
                """SELECT device_id, COALESCE(custom_name, hostname) AS hostname, device_ip,
                          device_mac_vendor, os_name, status
                   FROM Devices WHERE device_id = ?""", (item_id,)).fetchone()
            if device is None:
                return None
            from app.routes.devices import _latest_open_ports
            found = _latest_open_ports(connection).get(item_id)
            match = next((p for p in (found or {}).get("ports", []) if p["port"] == port), None)
            if match is None:
                return None
            kb = explain_port(port, match["protocol"], match["service"])
            return {
                "device": _device_facts(device),
                "open_port": {"number": port, "protocol": match["protocol"],
                              "service_reported_by_scan": match["service"],
                              "program_reported_by_scan": " ".join(
                                  x for x in (match["product"], match["version"]) if x) or "unknown"},
                "what_this_port_is": {"name": kb["name"], "what": kb["what"],
                                      "why_it_matters": kb["why"], "risk_level": kb["risk"]},
                "advice": [_article_brief(a) for a in kb["advice"] if get_article(a)],
            }

        if kind == "finding":
            row = connection.execute("""
                SELECT f.severity, f.title, f.detail, f.article_id, f.status,
                       COALESCE(d.custom_name, d.hostname) AS hostname, d.device_ip, d.device_mac_vendor, d.os_name, d.status AS device_status
                FROM Finding f JOIN Devices d ON d.device_id = f.device_id
                WHERE f.finding_id = ?""", (item_id,)).fetchone()
            if row is None:
                return None
            device = dict(row)
            device["status"] = row["device_status"]
            article = get_article(row["article_id"]) if row["article_id"] else None
            return {
                "recommendation": {"title": row["title"], "what_lanwatch_saw": row["detail"],
                                   "severity": row["severity"], "status": row["status"]},
                "device": _device_facts(device),
                "advice_article": {"why": article["why"], "steps": article["steps"],
                                   "difficulty": article["difficulty"]} if article else None,
            }

        if kind == "scan":
            from app.services.scan_summary import collect_scan_facts
            return collect_scan_facts(item_id)
    finally:
        connection.close()
    return None


def _article_brief(article_id):
    article = get_article(article_id)
    return {"title": article["title"], "why": article["why"], "first_step": article["steps"][0]}


# =============================================================================
# GROUP 4: REPORTS
# =============================================================================

@ai_api.route("/reports/generate", methods=["POST"])
def generate_report():
    """POST /api/reports/generate - the Reports page's AI report.
    Body (built by reports.js buildPayload): {report_type, period, payload}.
    Answers {"report": "..."}."""
    body = request.get_json(silent=True) or {}
    report_type = body.get("report_type")
    if report_type not in REPORT_TASKS or not isinstance(body.get("payload"), dict):
        return jsonify({"error": "Send report_type and payload as built by the Reports page."}), 400

    answer, code = _ask(f"{REPORT_TASKS[report_type]} The period covered is: {body.get('period', 'unknown')}.",
                        body["payload"], max_words=220)
    if code == 200:
        return jsonify({"report": answer["text"], "cached": answer["cached"]})
    return jsonify(answer), code


@ai_api.route("/reports/latest-summary", methods=["GET"])
def latest_summary():
    """GET /api/reports/latest-summary - the summary written after the latest full scan,
    or {"summary": null} if there hasn't been one yet."""
    connection = get_connection()
    row = connection.execute("""
        SELECT r.report_id, r.scan_id, r.tech_summary, r.ai_translation_summary, r.created_at,
               s.network_range
        FROM Report r LEFT JOIN Scans s ON s.scan_id = r.scan_id
        ORDER BY r.report_id DESC LIMIT 1""").fetchone()
    connection.close()
    return jsonify({"summary": dict(row) if row else None})
