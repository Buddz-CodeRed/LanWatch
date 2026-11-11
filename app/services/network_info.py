# =============================================================================
# FILE: app/services/network_info.py  -  WI-FI READER
# -----------------------------------------------------------------------------
# Reads which Wi-Fi network this computer is on, and how strong the signal is,
# using each operating system's own command.
#
# Flow:  dashboard.js loadNetworkInfo()
#        -> GET /api/network/info (routes/local_network.py)
#        -> get_connection_info()                         [GROUP 5]
#        -> get_wifi_state() picks the reader for this OS  [GROUP 5]
#        -> _get_wifi_windows() / _get_wifi_linux() / _get_wifi_macos()
#        -> dashboard header shows the Wi-Fi badge
#
# Every reader returns None (never crashes) when there is no Wi-Fi,
# the command is missing, or it takes longer than 10 seconds.
# =============================================================================

import os
import re
import subprocess
import sys

from app.init_database import get_connection

# =============================================================================
# GROUP 1: SETTINGS AND PATTERNS
# =============================================================================

# which operating system we are running on
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# stop a hung command from blocking the web request
SUBPROCESS_TIMEOUT_SECONDS = 10

# one line of "netsh wlan show interfaces", e.g. "    SSID                   : MyNetwork"
WIN_FIELD_LINE = re.compile(r"^\s{4}([A-Za-z][A-Za-z0-9 ()/]*?)\s{2,}: (.*)$")

# netsh labels -> the dictionary keys this app uses
WIN_FIELD_LABELS = {
    "Name": "interface_name",
    "Description": "adapter_description",
    "State": "state",
    "SSID": "ssid",
    "AP BSSID": "bssid",
    "Band": "band",
    "Channel": "channel",
    "Radio type": "radio_type",
    "Authentication": "authentication",
    "Signal": "signal",
    "Network type": "network_type",
}

MACOS_AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"


def _run(command):
    """Run a command and return its result, or None if it is missing or hangs."""
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              errors="replace", timeout=SUBPROCESS_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _to_int(text):
    """Turn "84" into 84; anything that isn't a whole number gives None."""
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


# =============================================================================
# GROUP 2: WINDOWS READER  (netsh wlan show interfaces)
# =============================================================================

def _get_wifi_windows():
    """Run netsh and turn its "Label : value" lines into a dictionary."""
    result = _run(["netsh", "wlan", "show", "interfaces"])
    if result is None or result.returncode != 0:
        return None

    fields = {}
    for line in result.stdout.splitlines():
        match = WIN_FIELD_LINE.match(line)
        if not match:
            continue
        label, value = match.group(1).strip(), match.group(2).strip()
        if label in WIN_FIELD_LABELS:
            fields[WIN_FIELD_LABELS[label]] = value

    # no SSID line = not connected to Wi-Fi
    if "ssid" not in fields:
        return None

    # "84%" -> 84
    digits = re.match(r"\d+", fields.get("signal", ""))
    fields["signal_percent"] = int(digits.group()) if digits else None

    # channel as a number when it is one
    if fields.get("channel"):
        fields["channel"] = _to_int(fields["channel"]) or fields["channel"]

    return fields


# =============================================================================
# GROUP 3: LINUX READER  (nmcli, then iwconfig)
# =============================================================================

def _get_wifi_linux():
    """Try NetworkManager (nmcli) first, then the older iwconfig."""

    # --- method 1: nmcli ---
    res = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID,SIGNAL,CHAN,SECURITY", "dev", "wifi"])
    if res is not None and res.returncode == 0:
        for line in res.stdout.splitlines():
            # nmcli -t escapes the colons inside MAC addresses as "\:"
            parts = line.replace(r"\:", "__COLON__").split(":")
            if len(parts) >= 5 and parts[0].lower() == "yes":
                bssid = parts[2].replace("__COLON__", ":")
                return {
                    "interface_name": "wlan0",
                    "adapter_description": "Wireless Interface",
                    "state": "connected",
                    "ssid": parts[1].replace("__COLON__", ":"),
                    "bssid": bssid.upper() if bssid else None,
                    "signal_percent": _to_int(parts[3]),
                    "channel": _to_int(parts[4]),
                    "authentication": parts[5].replace("__COLON__", ":") if len(parts) > 5 else None,
                }

    # --- method 2: iwconfig ---
    res = _run(["iwconfig"])
    if res is not None and res.returncode == 0:
        output = res.stdout
        ssid_match = re.search(r'ESSID:"([^"]+)"', output)
        bssid_match = re.search(r"Access Point:\s*([0-9A-Fa-f:]{17})", output)
        quality_match = re.search(r"Link Quality=(\d+)/(\d+)", output)

        if ssid_match:
            signal = None
            if quality_match:
                num, den = int(quality_match.group(1)), int(quality_match.group(2))
                signal = int((num / den) * 100) if den > 0 else None

            return {
                "interface_name": "wlan0",
                "adapter_description": "Wireless Interface",
                "state": "connected",
                "ssid": ssid_match.group(1),
                "bssid": bssid_match.group(1).upper() if bssid_match else None,
                "signal_percent": signal,
            }

    return None


# =============================================================================
# GROUP 4: MACOS READER  (airport, then networksetup)
# =============================================================================

def _get_wifi_macos():
    """Try Apple's airport tool first, then networksetup (name only)."""

    # --- method 1: airport -I ---
    if os.path.exists(MACOS_AIRPORT):
        res = _run([MACOS_AIRPORT, "-I"])
        if res is not None and res.returncode == 0:
            fields = {}
            for line in res.stdout.splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    fields[key.strip()] = val.strip()

            ssid = fields.get("SSID")
            if ssid and ssid != "off":
                # RSSI (e.g. -55 dBm) -> rough percentage (0-100)
                signal_percent = None
                rssi = fields.get("agrCtlRSSI", "")
                if re.fullmatch(r"-?\d+", rssi):
                    signal_percent = max(0, min(100, 2 * (int(rssi) + 100)))

                bssid = fields.get("BSSID")
                return {
                    "interface_name": "en0",
                    "adapter_description": "Wi-Fi Adapter",
                    "state": fields.get("state", "connected"),
                    "ssid": ssid,
                    "bssid": bssid.upper() if bssid else None,
                    "signal_percent": signal_percent,
                    "channel": _to_int(fields.get("channel", "").split(",")[0]),
                    "authentication": fields.get("link auth") or fields.get("802.11 auth"),
                }

    # --- method 2: networksetup ---
    res = _run(["networksetup", "-getairportnetwork", "en0"])
    marker = "Current Wi-Fi Network:"
    if res is not None and res.returncode == 0 and marker in res.stdout:
        ssid = res.stdout.split(marker)[1].strip()
        if ssid and not ssid.startswith("You are not"):
            return {
                "interface_name": "en0",
                "adapter_description": "Wi-Fi Adapter",
                "state": "connected",
                "ssid": ssid,
                "bssid": None,
                "signal_percent": None,
            }

    return None


# =============================================================================
# GROUP 5: PUBLIC FUNCTIONS (used by routes/local_network.py)
# =============================================================================

def get_wifi_state():
    """Return the current Wi-Fi details for this operating system, or None."""
    if IS_WINDOWS:
        return _get_wifi_windows()
    if IS_LINUX:
        return _get_wifi_linux()
    if IS_MACOS:
        return _get_wifi_macos()
    return None


def get_connection_info():
    """Return everything the dashboard's Wi-Fi badge needs. Never raises.

    If the router's MAC (bssid) matches a device LanWatch has already found,
    that device's name is added as "ap_in_device_list".
    """
    info = {
        "platform_supported": IS_WINDOWS or IS_LINUX or IS_MACOS,
        "wifi": get_wifi_state(),
        "ap_in_device_list": None,
    }

    wifi_data = info["wifi"]
    bssid = wifi_data.get("bssid") if wifi_data else None

    if bssid:
        try:
            connection = get_connection()
            try:
                row = connection.execute(
                    "SELECT hostname FROM Devices WHERE UPPER(device_mac) = ?",
                    (bssid.upper(),),
                ).fetchone()
            finally:
                connection.close()
            if row:
                info["ap_in_device_list"] = row["hostname"]
        except Exception:
            info["ap_in_device_list"] = None

    return info
