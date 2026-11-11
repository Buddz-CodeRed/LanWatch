# =============================================================================
# FILE: app/services/router_advice.py  -  "YOUR ROUTER" HARDENING GUIDE
# -----------------------------------------------------------------------------
# Builds the "Your router" section of the Security Advice page from two
# knowledge bases:
#   app/knowledge/routers.json  the hardening checklist + where to find each
#                               setting on common router brands
#   app/knowledge/isps.json     the routers common NZ internet providers supply,
#                               how to open them, and the provider's help page
#
# ADVICE ONLY: LanWatch never logs in to the router or changes it. The user
# follows the steps and ticks each one off.
#
# Flow:  advice.js loadRouter() -> GET /api/knowledge/router (routes/findings.py)
#        -> router_guide() -> _find_router() (which device is the router?)
#        -> _match_brand() / _match_models() -> checklist with ticks
#        user picks a provider / ticks a check -> PUT /api/knowledge/router
#        -> save_router_choices()
# =============================================================================

import json

from app.init_database import get_connection
from app.services.knowledge import _load

ISP_KEY = "router_isp"                  # System_Config: the provider the user picked
DONE_KEY = "router_checks_done"         # System_Config: JSON list of ticked check ids


# =============================================================================
# GROUP 1: SAVED CHOICES (System_Config)
# =============================================================================

def _get(connection, key):
    row = connection.execute("SELECT value FROM System_Config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set(connection, key, value):
    connection.execute("""INSERT INTO System_Config (key, value) VALUES (?, ?)
                          ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, value))


def _done_checks(connection):
    try:
        return set(json.loads(_get(connection, DONE_KEY) or "[]"))
    except ValueError:
        return set()


def save_router_choices(isp=None, done=None):
    """Save the provider the user picked ("" = not sure) and/or ticked checks.

    done is {check_id: True/False}. Raises ValueError for unknown ids.
    """
    isps, checks = _load("isps.json"), _load("routers.json")["checks"]
    if isp is not None and isp != "" and isp not in isps:
        raise ValueError("Unknown internet provider")
    if done is not None and (not isinstance(done, dict) or any(c not in checks for c in done)):
        raise ValueError("Unknown checklist item")

    connection = get_connection()
    try:
        if isp is not None:
            _set(connection, ISP_KEY, isp)
        if done:
            ticked = _done_checks(connection)
            for check_id, is_done in done.items():
                (ticked.add if is_done else ticked.discard)(check_id)
            _set(connection, DONE_KEY, json.dumps(sorted(ticked)))
        connection.commit()
    finally:
        connection.close()


# =============================================================================
# GROUP 2: RECOGNISING THE ROUTER
# =============================================================================

def _find_router(connection, gateways):
    """The Devices row of the router (the address this computer uses to reach
    the internet), or None if no scan has found it yet."""
    for ip in sorted(gateways):
        row = connection.execute("""
            SELECT device_id, device_ip, device_mac_vendor, hostname AS auto_name,
                   COALESCE(custom_name, hostname) AS hostname
            FROM Devices WHERE device_ip = ? ORDER BY last_detected DESC LIMIT 1""", (ip,)).fetchone()
        if row:
            return row
    return None


def _match_brand(vendor, name):
    """The routers.json brand id for this maker / name, or None."""
    vendor, name = (vendor or "").lower(), (name or "").lower()
    for brand_id, brand in _load("routers.json")["brands"].items():
        if any(v in vendor for v in brand["vendor_match"]):
            return brand_id
    for brand_id, brand in _load("routers.json")["brands"].items():
        if name and any(n in name for n in brand["name_match"]):
            return brand_id
    return None


def _match_models(name):
    """Every provider router whose name matches, as [(isp_id, router), ...]."""
    name = (name or "").lower()
    if not name:
        return []
    return [(isp_id, router)
            for isp_id, isp in _load("isps.json").items()
            for router in isp["routers"]
            if any(n in name for n in router["name_match"])]


# =============================================================================
# GROUP 3: THE GUIDE FOR THE PAGE
# =============================================================================

def router_guide(gateways=None):
    """Everything the "Your router" section shows:

    {router: {...} or None, brand: {...} or None, model: {...} or None,
     isp: {...} or None, isp_id, isp_guessed, isp_choices: [{id, name}],
     checks: [{id, title, difficulty, why, steps, where, done}], done_count}
    """
    if gateways is None:
        from app.services.net_scanner import default_gateways
        gateways = default_gateways()

    isps = _load("isps.json")
    knowledge = _load("routers.json")
    connection = get_connection()
    try:
        router = _find_router(connection, gateways)
        saved_isp = _get(connection, ISP_KEY)
        ticked = _done_checks(connection)
    finally:
        connection.close()

    names = " ".join(filter(None, [router["auto_name"], router["hostname"]])) if router else ""
    models = _match_models(names)
    brand_id = _match_brand(router["device_mac_vendor"] if router else None, names)
    if not models and brand_id:
        # no model in the name: if only ONE provider router is of this brand, it's probably that
        by_brand = [(i, r) for i, isp in _load("isps.json").items() for r in isp["routers"]
                    if r.get("brand") == brand_id]
        if len(by_brand) == 1:
            models = by_brand

    # the provider: the user's choice, otherwise a guess from the router's model
    isp_guessed = False
    isp_id = saved_isp if saved_isp in isps else None
    # (only when every matching model comes from the same provider)
    if saved_isp is None and models and len({i for i, _ in models}) == 1:
        isp_id, isp_guessed = models[0][0], True

    # the model: one that belongs to the chosen provider if possible
    model = next((r for i, r in models if i == isp_id), models[0][1] if models else None)
    if model and model.get("brand") and not brand_id:
        brand_id = model["brand"]
    brand = knowledge["brands"].get(brand_id) if brand_id else None

    checks = [{
        "id": check_id,
        "title": check["title"],
        "difficulty": check["difficulty"],
        "why": check["why"],
        "steps": check["steps"],
        "where": (brand or {}).get("where", {}).get(check_id),
        "done": check_id in ticked,
    } for check_id, check in knowledge["checks"].items()]

    return {
        "router": ({"device_id": router["device_id"], "ip": router["device_ip"],
                    "name": router["hostname"], "maker": router["device_mac_vendor"]}
                   if router else None),
        "gateways": sorted(gateways),
        "brand": ({"id": brand_id, "name": brand["name"], "open": brand["open"], "app": brand["app"]}
                  if brand else None),
        "model": model,
        "isp_id": isp_id,
        "isp_guessed": isp_guessed,
        "isp": isps.get(isp_id) if isp_id else None,
        "isp_choices": [{"id": i, "name": isp["name"]} for i, isp in isps.items()],
        "checks": checks,
        "done_count": sum(c["done"] for c in checks),
    }
