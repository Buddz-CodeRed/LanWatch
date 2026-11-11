# =============================================================================
# FILE: app/services/phone_listener.py  -  LETTING PHONES REACH LANWATCH
# -----------------------------------------------------------------------------
# LanWatch's main web server only listens on 127.0.0.1: only this computer can
# open it. When "Phones and tablets" is switched on (Settings), this file
# starts a SECOND web server for the same app on this computer's home-network
# address (e.g. http://192.168.1.20:5000), so phones on the same Wi-Fi can
# open it. Who may then actually see anything is decided by app/phone_access.py
# (sign-in, recognised devices only).
#
# "Only at home" is enforced three ways:
#   1. it only listens on network adapters Windows treats as a PRIVATE
#      network (home/work), never on Public ones such as cafe or hotel Wi-Fi
#   2. the Windows Firewall rule it adds only allows Private networks
#   3. it re-checks every CHECK_EVERY_SECONDS: if the computer moves to
#      another network, or the address changes, it follows (or stops)
# Switching the setting off stops the listener and removes the firewall rule.
# It runs inside LanWatch, so it stops whenever LanWatch stops.
#
# Flow:  create_app() -> start_phone_listener(app) -> watchdog thread
#        -> sync() every 20 s and straight after the setting changes
#           (routes/phone.py) -> _home_addresses() -> _start() / _stop()
#           -> _ensure_firewall_rule() / _remove_firewall_rule()
#        Settings page -> GET /api/phone/status -> status()
# =============================================================================

import ipaddress
import os
import platform
import subprocess
import sys
import threading
import time

CHECK_EVERY_SECONDS = 20
FIREWALL_RULE = "LanWatch - phones and tablets (home network)"
DEFAULT_PORT = 5000

_app = None
_server = None                 # the waitress server for the home network (or None)
_thread = None
_bound = ()                    # the addresses it listens on
_lock = threading.Lock()
_last = {"error": None, "firewall": None}
_profile_cache = {"at": 0, "value": {}}


# =============================================================================
# GROUP 1: WHICH ADDRESSES COUNT AS "HOME"?
# =============================================================================

def lanwatch_port():
    """The port LanWatch runs on (launcher.py sets LANWATCH_PORT)."""
    try:
        return int(os.environ.get("LANWATCH_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _run(command, timeout=15):
    try:
        return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _network_categories():
    """{adapter name: "Private" | "Public" | "DomainAuthenticated"} from Windows
    (cached for a few seconds; {} on other systems or if Windows can't say)."""
    if platform.system() != "Windows":
        return {}
    if time.time() - _profile_cache["at"] < 10:
        return _profile_cache["value"]
    result = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-NetConnectionProfile | ForEach-Object { $_.InterfaceAlias + '|' + $_.NetworkCategory }"])
    categories = {}
    for line in (result.stdout if result else "").splitlines():
        name, _, category = line.strip().rpartition("|")
        if name:
            categories[name] = category
    _profile_cache.update(at=time.time(), value=categories)
    return categories


def network_addresses():
    """Every private IPv4 address of this computer, with its adapter and how
    Windows treats that network: [{ip, adapter, category, home}]."""
    import psutil
    categories = _network_categories()
    stats = psutil.net_if_stats()
    found = []
    for adapter, addresses in psutil.net_if_addrs().items():
        if adapter in stats and not stats[adapter].isup:
            continue
        for address in addresses:
            if getattr(address.family, "name", "") != "AF_INET":
                continue
            ip = ipaddress.ip_address(address.address)
            if not ip.is_private or ip.is_loopback or ip.is_link_local:
                continue
            category = categories.get(adapter, "Unknown" if categories else "Not Windows")
            found.append({
                "ip": str(ip), "adapter": adapter, "category": category,
                # home = a Private (or work-domain) network; outside Windows: any private address
                "home": category in ("Private", "DomainAuthenticated", "Not Windows"),
            })
    return found


def _home_addresses():
    return tuple(sorted(a["ip"] for a in network_addresses() if a["home"]))


def _main_address(addresses):
    """The address phones should use: the one on the same network as the router."""
    try:
        from app.services.net_scanner import default_gateways
        gateways = default_gateways()
    except Exception:
        gateways = set()
    for ip in addresses:
        if any(ipaddress.ip_address(ip) in ipaddress.ip_network(f"{g}/24", strict=False) for g in gateways):
            return ip
    return addresses[0] if addresses else None


# =============================================================================
# GROUP 2: WINDOWS FIREWALL (Private networks only)
# =============================================================================

def _ensure_firewall_rule(port):
    """Add (or refresh) the rule that lets phones on a PRIVATE network reach
    LanWatch's port. Needs Administrator rights (LanWatch has them)."""
    if platform.system() != "Windows":
        return "not needed"
    _remove_firewall_rule()
    command = ["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}",
               "dir=in", "action=allow", "protocol=TCP", f"localport={port}",
               "profile=private,domain", "enable=yes"]
    if getattr(sys, "frozen", False):
        command.append(f"program={sys.executable}")       # only LanWatch.exe itself
    result = _run(command)
    if result and result.returncode == 0:
        return "ok"
    detail = ((result.stdout or result.stderr).strip() if result else "netsh couldn't be run")
    return "failed: " + (detail or "unknown error")


def _remove_firewall_rule():
    if platform.system() == "Windows":
        _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"])


# =============================================================================
# GROUP 3: STARTING AND STOPPING THE HOME-NETWORK SERVER
# =============================================================================

def _start(addresses, port):
    global _server, _thread, _bound
    from waitress.server import create_server
    listen = " ".join(f"{ip}:{port}" for ip in addresses)
    _server = create_server(_app, listen=listen, threads=4, ident="LanWatch")
    _thread = threading.Thread(target=_server.run, daemon=True, name="PhoneListener")
    _thread.start()
    _bound = addresses
    print(f"[phone access] listening on {listen}", flush=True)


def _stop():
    global _server, _thread, _bound
    if _server is not None:
        try:
            _server.close()
            _server.task_dispatcher.shutdown()
        except Exception as error:
            print(f"[phone access] stop problem: {error}", flush=True)
        if _thread is not None:
            _thread.join(5)
        print("[phone access] stopped", flush=True)
    _server, _thread, _bound = None, None, ()


def _enabled():
    from app.routes.settings import get_bool_setting
    return get_bool_setting("phone_access_enabled")


def sync():
    """Make the listener match the setting and the current network."""
    if _app is None:
        return
    with _lock:
        try:
            if not _enabled():
                if _server is not None or _last["firewall"] == "ok":
                    _stop()
                    _remove_firewall_rule()
                _last.update(error=None, firewall=None)
                return
            addresses = _home_addresses()
            if addresses == _bound and _server is not None:
                return
            _stop()
            if not addresses:
                _last["error"] = None          # status() explains why
                return
            port = lanwatch_port()
            _last["firewall"] = _ensure_firewall_rule(port)
            _start(addresses, port)
            _last["error"] = None
        except Exception as error:             # e.g. the port is used by another program
            _stop()
            _last["error"] = str(error)
            print(f"[phone access] couldn't start: {error}", flush=True)


def start_phone_listener(app):
    """Called once by create_app(): keep the listener in step with the setting."""
    global _app
    _app = app

    def watchdog():
        while True:
            sync()
            time.sleep(CHECK_EVERY_SECONDS)

    threading.Thread(target=watchdog, daemon=True, name="PhoneListenerWatchdog").start()


# =============================================================================
# GROUP 4: STATUS FOR THE SETTINGS PAGE
# =============================================================================

def status():
    """What the Settings page shows: is it on, which address to use, and
    anything stopping phones from connecting."""
    addresses = network_addresses()
    enabled = _enabled()
    with _lock:
        running = _server is not None
        bound = list(_bound)
        error, firewall = _last["error"], _last["firewall"]
    port = lanwatch_port()
    main = _main_address(bound) if bound else None

    problems = []
    if enabled:
        if error:
            problems.append(f"LanWatch couldn't open its home-network address: {error}")
        if not bound and not error:
            if any(a["category"] == "Public" for a in addresses):
                problems.append("Windows treats this computer's network as Public, so LanWatch won't accept phones on it. "
                                "If this is your home network, change it in Windows Settings > Network & internet > "
                                "(your Wi-Fi or Ethernet) > Network profile type > Private network.")
            else:
                problems.append("This computer isn't connected to a home network right now.")
        if firewall and firewall.startswith("failed"):
            problems.append("The Windows Firewall rule couldn't be added: " + firewall[8:].rstrip(". ") +
                            ". Phones may be blocked. Is LanWatch running with Administrator rights?")

    return {
        "enabled": enabled,
        "running": running,
        "port": port,
        "url": f"http://{main}:{port}/" if main else None,
        "addresses": [dict(a, used=a["ip"] in bound) for a in addresses],
        "firewall": firewall,
        "problems": problems,
    }
