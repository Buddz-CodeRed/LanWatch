# =============================================================================
# FILE: app/routes/local_network.py  -  WI-FI INFO API  (/api/network)
# -----------------------------------------------------------------------------
# Flow:  dashboard.js loadNetworkInfo()
#        -> GET /api/network/info -> get_info()
#        -> services/network_info.py get_connection_info()
#        -> dashboard header shows the Wi-Fi badge (name + signal %)
# Flow:  dashboard.js loadSystemCheck()
#        -> GET /api/network/system-check -> system_check()
#        -> services/system_check.py run_checks()
#        -> a warning bar on the dashboard if Nmap, Npcap or Administrator rights are missing
# =============================================================================

from flask import Blueprint, jsonify

from app.services.network_info import get_connection_info
from app.services.system_check import run_checks

local_network = Blueprint("local_network", __name__, url_prefix="/api/network")


# =============================================================================
# GROUP 1: CURRENT WI-FI CONNECTION
# =============================================================================

@local_network.route("/info", methods=["GET"])
def get_info():
    """GET /api/network/info - current Wi-Fi name, signal, channel, etc."""
    return jsonify(get_connection_info())


# =============================================================================
# GROUP 2: IS EVERYTHING LANWATCH NEEDS INSTALLED?
# =============================================================================

@local_network.route("/system-check", methods=["GET"])
def system_check():
    """GET /api/network/system-check - Nmap, Npcap, Administrator rights:
    {"ok": bool, "checks": [{id, ok, title, detail}]}."""
    return jsonify(run_checks())
