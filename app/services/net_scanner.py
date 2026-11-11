# =============================================================================
# FILE: app/services/net_scanner.py  -  NETWORK SCANNER (talks to nmap)
# -----------------------------------------------------------------------------
# Finds the devices on a network range and describes each one.
#
# Two kinds of scan (see SCAN_PROFILES below):
#   quick - who is on the network: names, makers, OS guess (a minute or two)
#   full  - everything quick finds PLUS every open port and the program
#           ("service") answering on it, e.g. 443 https nginx 1.24 (much longer)
#
# Flow:  user picks Quick/Full and clicks "Run Scan" (dashboard.js runScan)
#        or the scheduler starts one (services/scan_scheduler.py)
#        -> POST /api/scans/ (routes/scans.py run_scan / start_scan)
#        -> validate_network_target()            [GROUP 1, fails fast on bad input]
#        -> background thread calls discover_devices(network, scan_type=...)  [GROUP 3]
#        -> results (and, for full scans, open ports) saved by routes/scans.py
#
#        The dashboard page also calls detect_local_subnets() [GROUP 2]
#        (via app/__init__.py) to pre-fill the scan box.
# =============================================================================

import ipaddress
import re
import socket
import threading

import nmap
import psutil
from mac_vendor_lookup import BaseMacLookup, MacLookup

from app.paths import data_path
from app.services.mdns_names import mdns_names
from app.services.system_check import find_nmap

# the manufacturer list (downloaded once from the IEEE by mac_vendor_lookup)
# is kept in LanWatch's data folder, not in the user's profile. If it can't be
# downloaded, nmap's own built-in manufacturer list is still used.
BaseMacLookup.cache_path = data_path("mac-vendors.txt")

# --- tuning / patterns -------------------------------------------------------

# a single ip (192.168.1.5), a cidr range (192.168.1.0/24)
# or a host range (192.168.1.1-254)
TARGET_PATTERN = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2}|-\d{1,3})?$")

# NetBIOS (Windows computer) name pulled out of the nbstat script output
NETBIOS_NAME_PATTERN = re.compile(r"(?:Device name):\s*([^\r\n]+)")

# reverse DNS lookups give up after this many seconds so one slow
# lookup cannot stall a scan
REVERSE_DNS_TIMEOUT_SECONDS = 2.0

# nmap options for each scan type, and how long the whole scan may take.
#   -O                    guess the operating system
#   -F                    quick: only the 100 most common ports
#   --top-ports 1000      full: the 1,000 most common ports
#   -sV --version-light   full: ask each open port which program is answering
#   -T4                   fast timing (fine on a home network)
#   --host-timeout        give up on one slow device after this long
#   --script nbstat.nse   ask Windows PCs for their computer name
SCAN_PROFILES = {
    "quick": {
        "arguments": "-O -F -T4 --host-timeout 120s --script nbstat.nse",
        "timeout_seconds": 300,          # 5 minutes for the whole scan
    },
    "full": {
        "arguments": "-O -sV --version-light --top-ports 1000 -T4 "
                     "--host-timeout 300s --script nbstat.nse",
        "timeout_seconds": 1800,         # 30 minutes for the whole scan
    },
}
SCAN_TYPES = tuple(SCAN_PROFILES)        # ("quick", "full")


# =============================================================================
# GROUP 1: INPUT VALIDATION
# Checks the network range typed by the user before any slow work starts.
# =============================================================================

def validate_network_target(network):
    """Raise ValueError (with a readable message) if `network` isn't usable.

    Called by routes/scans.py before a scan starts, and again by
    discover_devices() as a safety net.
    """
    if not TARGET_PATTERN.match(network):
        raise ValueError(
            "Network range must look like 192.168.1.0/24, "
            "192.168.1.5 or 192.168.1.1-254"
        )

    # every part of the address (and the end of a host range) must be 0-255
    base_ip = network.split("/")[0].split("-")[0]
    numbers = [int(octet) for octet in base_ip.split(".")]
    if "-" in network:
        numbers.append(int(network.split("-")[1]))

    if any(number > 255 for number in numbers):
        raise ValueError("Each part of the IP address must be between 0 and 255")

    # optional cidr prefix (/24) must fit in 0-32
    if "/" in network and int(network.split("/")[1]) > 32:
        raise ValueError("The subnet prefix must be between 0 and 32")


# =============================================================================
# GROUP 2: LOCAL NETWORK DETECTION
# Works out which network(s) this computer is connected to, so the dashboard
# can suggest the right range to scan.
# =============================================================================

def _default_route_source_ip():
    """Return the local IP this computer uses to reach the internet (or None).

    "Connecting" a UDP socket sends no packets; it only makes Windows choose
    which of our addresses it would use, which we then read back.
    """
    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        probe_socket.connect(("8.8.8.8", 80))
        return probe_socket.getsockname()[0]
    except OSError:
        return None
    finally:
        probe_socket.close()


def detect_local_subnets():
    """Return the IPv4 networks attached to this computer, best guess first.

    Used by app/__init__.py to fill the dashboard scan box and its suggestions,
    and by services/scan_scheduler.py when no default network is saved.
    """
    default_source_ip = _default_route_source_ip()
    candidates = []
    seen_subnets = set()

    interface_stats = psutil.net_if_stats()

    for interface_name, addresses in psutil.net_if_addrs().items():
        stats = interface_stats.get(interface_name)

        # skip adapters that are switched off
        if stats is not None and not stats.isup:
            continue

        for address in addresses:

            # only look at IPv4 addresses
            if address.family != socket.AF_INET:
                continue

            ip = address.address

            # skip loopback and "no network" (link local) addresses
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue

            if not address.netmask:
                continue

            try:
                network = ipaddress.ip_network(f"{ip}/{address.netmask}", strict=False)
            except ValueError:
                continue

            # a /32 "subnet" is a single address, not useful as a target
            if network.num_addresses <= 1:
                continue

            cidr = str(network)
            if cidr in seen_subnets:
                continue
            seen_subnets.add(cidr)

            # the network used to reach the internet is ranked first (0)
            is_default_route_subnet = (
                default_source_ip is not None
                and ipaddress.ip_address(default_source_ip) in network
            )
            candidates.append((0 if is_default_route_subnet else 1, cidr))

    # sorting puts rank 0 (the internet-facing network) before the others
    candidates.sort()

    return [cidr for _, cidr in candidates]


def default_gateways():
    """The router address(es) this computer uses to reach the internet, e.g. {"192.168.1.1"}.

    Used by services/findings.py to recognise which device is the router
    (router-specific advice such as "check the admin password").
    Asks the operating system's own routing table; returns an empty set if it can't tell.
    """
    import platform
    import subprocess

    def run(command):
        try:
            return subprocess.run(command, capture_output=True, text=True, errors="replace",
                                  timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    gateways = set()
    system = platform.system()

    if system == "Windows":
        # lines look like:  0.0.0.0   0.0.0.0   192.168.1.1   192.168.1.100   25
        for line in run(["route", "print", "-4", "0.0.0.0"]).splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                gateways.add(parts[2])
    elif system == "Linux":
        # "default via 192.168.1.1 dev wlan0 ..."
        for line in run(["ip", "-4", "route", "show", "default"]).splitlines():
            parts = line.split()
            if "via" in parts:
                gateways.add(parts[parts.index("via") + 1])
    elif system == "Darwin":
        # "    gateway: 192.168.1.1"
        for line in run(["route", "-n", "get", "default"]).splitlines():
            if line.strip().startswith("gateway:"):
                gateways.add(line.split(":", 1)[1].strip())

    # keep real IPv4 addresses only ("On-link" etc. are dropped)
    valid = set()
    for gateway in gateways:
        try:
            if ipaddress.ip_address(gateway).version == 4:
                valid.add(gateway)
        except ValueError:
            pass
    return valid


# =============================================================================
# GROUP 3: DEVICE DISCOVERY (THE SCAN ITSELF)
# Runs nmap, then builds one tidy dictionary per device found.
# =============================================================================

_mac_lookup = None   # created on first use (loads the manufacturer list once)


def _lookup_vendor(mac_address):
    """Return the manufacturer for a MAC address from the offline list, or None."""
    global _mac_lookup
    try:
        if _mac_lookup is None:
            _mac_lookup = MacLookup()
        return _mac_lookup.lookup(mac_address)
    except Exception:
        # unknown manufacturer, or the vendor list isn't available
        return None


def _reverse_lookup(ip):
    """Ask DNS for the name of `ip`, giving up after REVERSE_DNS_TIMEOUT_SECONDS.

    socket.gethostbyaddr can block for many seconds when nobody answers, so it
    runs in a worker thread that is simply abandoned if it takes too long.
    """
    result = {}

    def lookup_worker():
        try:
            result["hostname"] = socket.gethostbyaddr(ip)[0]
        except OSError:
            result["hostname"] = None

    worker = threading.Thread(target=lookup_worker, daemon=True)
    worker.start()
    worker.join(REVERSE_DNS_TIMEOUT_SECONDS)

    return result.get("hostname")


def _netbios_name(host_result):
    """Return the Windows computer name from the nbstat script output, or None."""
    for script in host_result.get("hostscript", []):
        if script.get("id") == "nbstat":
            match = NETBIOS_NAME_PATTERN.search(script.get("output", ""))
            if match:
                return match.group(1).strip()
    return None


# endings that only say "on this network": "iPad-4.local" -> "iPad-4"
LOCAL_SUFFIXES = (".local", ".home.arpa", ".home", ".lan", ".localdomain", ".domain", ".internal")
FALLBACK_NAME = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|.+ Device \(.+\)|Host \(.+\))$")


def clean_name(name, ip):
    """A tidy display name, or None if `name` is empty or just the address."""
    if not name:
        return None
    name = name.strip().rstrip(".")
    for suffix in LOCAL_SUFFIXES:
        if name.lower().endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    if not name or name == ip or name.replace("-", ".") == ip:
        return None
    return name


def is_fallback_name(name):
    """True for names LanWatch made up because the device gave none
    ("Apple Device (192.168.1.5)", "Host (...)", or a bare address)."""
    return not name or bool(FALLBACK_NAME.match(name))


# how trustworthy each kind of name is (a name may only be replaced by one
# ranked the same or higher: services/../routes/scans.py _upsert_device)
NAME_RANK = {"netbios": 3, "dns": 2, "mdns": 1, "fallback": 0}


def _best_device_name(host, netbios_name, hostname, mdns, vendor, os_name):
    """Pick a display name with a fallback chain, so a device is never nameless.
    Returns (name, source): source is "netbios", "dns", "mdns" or "fallback".

    Windows name (NetBIOS) -> router's name for it (DNS) -> mDNS/Bonjour name
    -> "Vendor Device (ip)" -> "OS Device (ip)" -> "Host (ip)"
    """
    for source, candidate in (("netbios", netbios_name), ("dns", hostname), ("mdns", mdns)):
        name = clean_name(candidate, host)
        if name:
            return name, source
    if vendor and vendor != "Unknown":
        return f"{vendor} Device ({host})", "fallback"
    if os_name and os_name != "Unknown":
        return f"{os_name.split()[0]} Device ({host})", "fallback"
    return f"Host ({host})", "fallback"


def _open_ports(host_result):
    """Every OPEN port nmap found on one device, as a list of dictionaries:
    {"port": 443, "protocol": "tcp", "service": "https", "product": "nginx", "version": "1.24"}

    Ports reported as "closed" or "filtered" are left out: only open ones
    matter for security. Product/version are only filled in by full scans (-sV).
    """
    ports = []
    for protocol in ("tcp", "udp"):
        for port, info in host_result.get(protocol, {}).items():
            if info.get("state") != "open":
                continue
            ports.append({
                "port": int(port),
                "protocol": protocol,
                "service": info.get("name") or None,
                "product": info.get("product") or None,
                "version": info.get("version") or None,
            })
    ports.sort(key=lambda p: (p["protocol"], p["port"]))
    return ports


def discover_devices(network, timeout=None, scan_type="quick"):
    """Scan `network` with nmap and return a list of device dictionaries.

    scan_type is "quick" or "full" (see SCAN_PROFILES). Every device includes
    "open_ports"; routes/scans.py only stores them for full scans, because a
    quick scan only checks 100 ports and would give an incomplete picture.

    Called by routes/scans.py (_execute_scan) in a background thread.
    Raises TimeoutError if nmap runs longer than `timeout` seconds (default:
    the profile's limit) and RuntimeError if nmap itself fails.
    """
    validate_network_target(network)
    if scan_type not in SCAN_PROFILES:
        raise ValueError(f"Unknown scan type: {scan_type}")
    profile = SCAN_PROFILES[scan_type]
    timeout = timeout or profile["timeout_seconds"]

    # find nmap even when it isn't on the PATH (e.g. the installed app)
    nmap_path = find_nmap()
    if nmap_path is None:
        raise RuntimeError("Nmap isn't installed, so LanWatch can't scan. "
                           "Install it from nmap.org (with Npcap), then restart LanWatch.")
    scanner = nmap.PortScanner(nmap_search_path=(nmap_path,))

    try:
        scanner.scan(network, arguments=profile["arguments"], timeout=timeout)

    # PortScannerTimeout is a kind of PortScannerError, so it is caught first
    except nmap.PortScannerTimeout:
        raise TimeoutError(
            f"Nmap did not finish within {timeout} seconds, try a smaller "
            "network range (for example a single /24)"
        )
    except nmap.PortScannerError as error:
        raise RuntimeError(f"Nmap scan failed: {error}") from error

    devices = []

    # ask every device for its mDNS (Bonjour) name, all at once (~1.5 s in total)
    announced = mdns_names(scanner.all_hosts())

    for host in scanner.all_hosts():
        host_result = scanner[host]

        # MAC address (only visible for devices on the same local network)
        mac_address = host_result.get("addresses", {}).get("mac")

        # manufacturer: nmap's own guess first, then the offline vendor list
        vendor = None
        if mac_address:
            vendor = host_result.get("vendor", {}).get(mac_address) or _lookup_vendor(mac_address)

        # hostname: nmap's reverse DNS first, then our own time-limited lookup
        hostname = host_result.hostname() or _reverse_lookup(host)

        # operating system guess (from the -O option)
        os_matches = host_result.get("osmatch", [])
        os_name = os_matches[0].get("name", "Unknown") if os_matches else "Unknown"

        name, name_source = _best_device_name(
            host, _netbios_name(host_result), hostname, announced.get(host), vendor, os_name)

        devices.append({
            "device_ip": host,
            "device_name": name,                 # the best name found (see _best_device_name)
            "name_source": name_source,          # netbios / dns / mdns / fallback
            "hostname": hostname,
            "device_mac": mac_address,
            "device_mac_vendor": vendor,
            "os_name": os_name,
            "status": "active",
            "open_ports": _open_ports(host_result),
        })

    return devices


# =============================================================================
# GROUP 4: SELF-TEST
# Only runs when this file is started directly:
#     python -m app.services.net_scanner   (as Administrator)
# =============================================================================

if __name__ == "__main__":
    # add "full" to the command for a full scan:  python -m app.services.net_scanner full
    import sys
    chosen_type = sys.argv[1] if len(sys.argv) > 1 else "quick"

    subnets = detect_local_subnets()
    print("Detected local subnets:", subnets)

    if subnets:
        target = subnets[0]     # the internet-facing network
        print(f"\n{chosen_type.title()} scan of primary subnet ({target})...\n")
        for device in discover_devices(target, scan_type=chosen_type):
            print(device)
    else:
        print("No active network subnets found.")
