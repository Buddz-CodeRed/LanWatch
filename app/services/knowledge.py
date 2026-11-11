# =============================================================================
# FILE: app/services/knowledge.py  -  THE KNOWLEDGE BASE (hand-written facts)
# -----------------------------------------------------------------------------
# Plain-English explanations written and checked by people:
#   app/knowledge/ports.json        what each common port is and how risky it is
#   app/knowledge/remediation.json  advice articles (what to do, step by step)
#   ALERT_TYPES (below)             what each kind of LanWatch alert means
#   app/knowledge/routers.json      router hardening checklist + common router brands
#   app/knowledge/isps.json         routers supplied by common NZ internet providers
#                                   (both used by services/router_advice.py)
#
# This is the "truth" LanWatch shows. The local AI (services/ai_translator.py)
# only REWORDS these facts for the user; it never decides what is risky.
# Everything here works with the AI switched off.
#
# Used by: routes/devices.py (port explanations on the Devices page),
#          services/findings.py (advice articles), routes/ai.py (AI facts),
#          routes/findings.py (GET /api/knowledge/ports/<port>)
# =============================================================================

import json
import os
import sys
from functools import lru_cache

RISK_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}


# =============================================================================
# GROUP 1: LOADING THE FILES (once, then kept in memory)
# =============================================================================

def _knowledge_dir():
    """app/knowledge, or the copy inside the installed app (app/paths.py)."""
    from app.paths import resource_path
    return resource_path("app", "knowledge")


@lru_cache(maxsize=None)
def _load(filename):
    """Read one JSON file; keys starting with '_' are notes for people, not data."""
    with open(os.path.join(_knowledge_dir(), filename), encoding="utf-8") as file:
        data = json.load(file)
    return {key: value for key, value in data.items() if not key.startswith("_")}


# =============================================================================
# GROUP 2: PORTS
# =============================================================================

def explain_port(port, protocol="tcp", service=None):
    """The knowledge-base entry for one port, always with the same fields:
    {port, protocol, name, risk, what, why, advice:[ids], known: True/False}

    Unknown ports get a careful generic entry (risk "low") that uses nmap's
    service name if there is one.
    """
    entry = _load("ports.json").get(str(port))
    if entry and entry.get("protocol", "tcp") == protocol:
        return {"port": int(port), "protocol": protocol, "known": True,
                "name": entry["name"], "risk": entry["risk"], "what": entry["what"],
                "why": entry["why"], "advice": list(entry.get("advice", []))}

    label = f"'{service}'" if service and service != "unknown" else "an uncommon service"
    return {
        "port": int(port), "protocol": protocol, "known": False,
        "name": f"{service} (port {port})" if service and service != "unknown" else f"Port {port}",
        "risk": "low",
        "what": f"This port is used by {label}. It isn't in LanWatch's list of common home-network ports.",
        "why": "An unexpected open port isn't necessarily a problem, but it's worth knowing which program on this device uses it.",
        "advice": ["review_unknown_service"],
    }


# =============================================================================
# GROUP 3: ADVICE ARTICLES
# =============================================================================

def get_article(article_id):
    """One advice article {id, title, difficulty, why, steps:[...]}, or None."""
    article = _load("remediation.json").get(article_id)
    return dict(article, id=article_id) if article else None


# =============================================================================
# GROUP 4: ALERT MEANINGS
# The same wording as alerts.js describe(), so the page and the AI agree.
# =============================================================================

ALERT_TYPES = {
    "bandwidth_spike_out": {
        "title": "Sending much more data than usual",
        "meaning": "This device is uploading far more data than it normally does.",
        "common_harmless_causes": "backing up photos or files to the cloud, uploading videos, video calls",
        "what_to_check": "whether someone is backing up or uploading something big on this device; if not, check what is running on it",
    },
    "bandwidth_spike_in": {
        "title": "Receiving much more data than usual",
        "meaning": "This device is downloading far more data than it normally does.",
        "common_harmless_causes": "software or game updates, streaming in high quality, large downloads",
        "what_to_check": "whether an update or big download is happening on this device",
    },
    "packet_spike_out": {
        "title": "Unusually busy network activity (sending)",
        "meaning": "This device is sending far more small network messages than it normally does.",
        "common_harmless_causes": "software updates, online games, apps syncing",
        "what_to_check": "whether the device is updating or a new app was installed; if it keeps happening, check what is running on it",
    },
    "new_device": {
        "title": "New device joined your network",
        "meaning": "A device LanWatch has never seen before connected to your home network.",
        "common_harmless_causes": "a new phone, laptop or smart-home gadget; a visitor on your Wi-Fi; "
                                  "a phone that changed its private Wi-Fi address",
        "what_to_check": "whether you or someone at home recognises it; if not, change your Wi-Fi password",
    },
    "new_open_port": {
        "title": "New open port on a device",
        "meaning": "A device on your network started accepting connections on a port it didn't use before.",
        "common_harmless_causes": "a new app, a software update, or a feature switched on in the device's settings",
        "what_to_check": "whether someone changed this device's settings or installed something new; see Security Advice",
    },
    "packet_spike_in": {
        "title": "Unusually busy network activity (receiving)",
        "meaning": "This device is receiving far more small network messages than it normally does.",
        "common_harmless_causes": "software updates, online games, apps syncing",
        "what_to_check": "whether the device is updating or busy with an app",
    },
}


def explain_alert_type(alert_type):
    """What an alert type means, or a neutral fallback for unknown types."""
    return ALERT_TYPES.get(alert_type, {
        "title": "Unusual activity detected",
        "meaning": "LanWatch noticed something different from this device's normal behaviour.",
        "common_harmless_causes": "unknown",
        "what_to_check": "the technical details of the alert",
    })
