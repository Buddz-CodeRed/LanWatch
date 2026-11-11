# =============================================================================
# FILE: app/services/mdns_names.py  -  ASK DEVICES FOR THEIR NAME (mDNS / Bonjour)
# -----------------------------------------------------------------------------
# Many home devices announce a name over mDNS ("multicast DNS", Apple calls it
# Bonjour): iPhones and iPads, Macs, many Android devices, printers,
# Chromecasts and Windows 10/11. This sends each device ONE small question,
# "what is the name for your address?" (a reverse 'PTR' question sent straight
# to the device's mDNS port, UDP 5353), and reads the answer, e.g.
#     192.168.1.129  ->  "iPad-4.local"
#
# Devices that don't use mDNS, or block it, simply don't answer; after
# TIMEOUT_SECONDS LanWatch moves on. All devices are asked at the same time,
# so a whole network takes about TIMEOUT_SECONDS in total.
#
# Used by: services/net_scanner.py discover_devices() (every quick and full scan)
# No extra packages: plain Python sockets.
# =============================================================================

import socket
import struct
from concurrent.futures import ThreadPoolExecutor

MDNS_PORT = 5353
TIMEOUT_SECONDS = 1.5
MAX_PARALLEL = 32
PTR = 12                     # DNS record type: "pointer" (address -> name)
CLASS_IN_UNICAST = 0x8001    # class IN + "please answer me directly" bit


# =============================================================================
# GROUP 1: BUILDING THE QUESTION AND READING THE ANSWER
# =============================================================================

def _question(ip):
    """The DNS question for "1.129.168.192.in-addr.arpa", as bytes."""
    name = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    qname = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split(".")) + b"\x00"
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)          # id, flags, 1 question
    return header + qname + struct.pack(">HH", PTR, CLASS_IN_UNICAST)


def _read_name(data, offset):
    """Read a DNS name at `offset` (following compression pointers).
    Returns (name, offset just after it)."""
    labels, end, jumps = [], None, 0
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:                                # pointer to an earlier name
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            jumps += 1
            if jumps > 20:
                raise ValueError("DNS name pointer loop")
            continue
        labels.append(data[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length
    return ".".join(labels), (end if end is not None else offset)


def _first_ptr_answer(data):
    """The name in the first PTR answer of a DNS reply, or None."""
    questions, answers = struct.unpack(">HH", data[4:8])
    offset = 12
    for _ in range(questions):
        _, offset = _read_name(data, offset)
        offset += 4
    for _ in range(answers):
        _, offset = _read_name(data, offset)
        rtype, _, _, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if rtype == PTR:
            return _read_name(data, offset)[0]
        offset += rdlength
    return None


# =============================================================================
# GROUP 2: ASKING DEVICES
# =============================================================================

def mdns_name(ip, timeout=TIMEOUT_SECONDS):
    """The name `ip` announces over mDNS (e.g. "iPad-4.local"), or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(_question(ip), (ip, MDNS_PORT))
        data, sender = sock.recvfrom(4096)
        if sender[0] != ip:
            return None
        return _first_ptr_answer(data)
    except (OSError, ValueError, IndexError, struct.error):
        return None                      # no answer, blocked, or a reply we can't read
    finally:
        sock.close()


def mdns_names(ips):
    """{ip: name} for every device in `ips` that answered (all asked at once)."""
    ips = list(ips)
    if not ips:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(ips))) as pool:
        answers = pool.map(mdns_name, ips)
    return {ip: name for ip, name in zip(ips, answers) if name}
