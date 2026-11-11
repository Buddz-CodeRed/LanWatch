# =============================================================================
# FILE: app/services/network_monitor.py  -  TRAFFIC MONITOR + SPIKE DETECTOR
# -----------------------------------------------------------------------------
# Runs in the background for as long as LanWatch is open:
#   1. a packet sniffer counts bytes/packets per IP address        [GROUP 2]
#   2. every few seconds each active device's counts are checked
#      against its recent average and saved to the Traffic table   [GROUP 3, 4]
#   3. a big jump raises an alert in the Alert table               [GROUP 3]
#
# Flow:  create_app() (app/__init__.py) -> traffic_monitor_loop()  [GROUP 5]
#        -> thread 1: _start_packet_sniffer() keeps the counters up to date
#        -> thread 2: monitor_loop() every <interval> seconds
#             -> record_device_traffic() for each active device
#                  -> _evaluate_device_spike() -> _raise_alert()
#                  -> INSERT INTO Traffic
#        The pages then read these tables through /api/devices, /api/usage
#        and /api/alerts.
#
# The interval comes from the Settings page ("Traffic sampling interval",
# System_Config key "traffic_sample_interval_seconds", default 5 seconds).
# =============================================================================

import threading
import time
from collections import defaultdict

from app.init_database import get_connection


# =============================================================================
# GROUP 1: SHARED COUNTERS AND TUNING
# =============================================================================

# live counters: filled by the sniffer, emptied by the monitor loop
_ip_bytes_sent = defaultdict(int)
_ip_bytes_recv = defaultdict(int)
_ip_pkts_sent = defaultdict(int)
_ip_pkts_recv = defaultdict(int)
_counter_lock = threading.Lock()    # stops both threads touching the counters at once

# sampling interval (keep MIN/MAX in step with routes/settings.py)
SAMPLE_INTERVAL = 5                 # default seconds between samples (Settings page overrides it)
MIN_SAMPLE_INTERVAL = 1
MAX_SAMPLE_INTERVAL = 300
SAMPLE_INTERVAL_KEY = "traffic_sample_interval_seconds"

# spike detection
SPIKE_THRESHOLD_MULTIPLIER = 3.0    # alert when current rate >= 3x baseline
CRITICAL_MULTIPLIER = 5.0           # 5x or more raises severity one level
BASELINE_MIN_WINDOW_SECONDS = 3600  # baseline looks back at least this far (1 hour)
MIN_BASELINE_SAMPLES = 20           # samples needed before alerting (about 100s at the default 5s)
MIN_SPIKE_BYTES_PER_SEC = 20_000    # ignore spikes smaller than this
MIN_SPIKE_PKTS_PER_SEC = 100


def _read_sample_interval(fallback):
    """Current sampling interval in seconds, read from System_Config.

    - never saved on the Settings page -> SAMPLE_INTERVAL (5)
    - value outside the allowed range  -> clamped into it
    - database busy or value unreadable -> `fallback` (the interval already in
      use), so a hiccup can never stop or change the monitor by accident
    """
    try:
        connection = get_connection()
        try:
            row = connection.execute(
                "SELECT value FROM System_Config WHERE key = ?", (SAMPLE_INTERVAL_KEY,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return SAMPLE_INTERVAL
        return max(MIN_SAMPLE_INTERVAL, min(MAX_SAMPLE_INTERVAL, int(row["value"])))
    except Exception:
        return fallback


# =============================================================================
# GROUP 2: PACKET SNIFFER
# Watches every packet on the network adapter and adds it to the counters.
# Needs Npcap + Administrator rights; without them traffic just stays at 0.
# =============================================================================

def _start_packet_sniffer():
    """Run Scapy's sniffer forever, counting bytes and packets per IP address."""
    try:
        from scapy.all import sniff, IP

        def process_packet(packet):
            """Called by Scapy for every packet: credit sender and receiver."""
            if IP in packet:
                src_ip = packet[IP].src
                dst_ip = packet[IP].dst
                pkt_len = len(packet)

                with _counter_lock:
                    _ip_bytes_sent[src_ip] += pkt_len
                    _ip_bytes_recv[dst_ip] += pkt_len
                    _ip_pkts_sent[src_ip] += 1
                    _ip_pkts_recv[dst_ip] += 1

        sniff(prn=process_packet, store=False)
    except Exception as e:
        print(f"[Packet Sniffer Error / admin rights and Npcap required]: {e}")


# =============================================================================
# GROUP 3: SPIKE DETECTION AND ALERTS
# Compares a device's current rate with its recent average.
# =============================================================================

def _baseline_window(interval):
    """SQLite time modifier (e.g. "-3600 seconds") for how far back "normal" looks.

    A fixed hour holds fewer than MIN_BASELINE_SAMPLES samples once the interval
    is above 180 s, which would silently switch alerts off, so the window grows
    with the interval: always room for 3x the minimum samples.
    """
    seconds = max(BASELINE_MIN_WINDOW_SECONDS, int(interval) * MIN_BASELINE_SAMPLES * 3)
    return f"-{seconds} seconds"


def _fmt_bytes_rate(bps):
    """Format bytes per second for alert messages, e.g. 1.2 MB/s."""
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _raise_alert(cursor, device_id, alert_type, severity, message):
    """Save an alert, unless an unresolved one of the same type already exists
    for this device (so one long download doesn't create hundreds of alerts)."""
    existing = cursor.execute(
        """SELECT 1 FROM Alert
            WHERE device_id = ? AND alert_type = ? AND is_resolved = 0
            LIMIT 1""",
        (device_id, alert_type),
    ).fetchone()
    if existing:
        return
    cursor.execute(
        """INSERT INTO Alert (device_id, alert_type, severity, message)
            VALUES (?, ?, ?, ?)""",
        (device_id, alert_type, severity, message),
    )


def _evaluate_device_spike(cursor, device_id, interval,
                           bytes_sent, bytes_received, packets_sent, packets_received):
    """Check four rates (data out/in, packets out/in) against the device's average.

    Must run BEFORE the current sample is saved, so a spike can't hide itself
    by raising its own average.
    """
    base = cursor.execute(
        """SELECT COUNT(*) AS samples,
                  AVG(bytes_sent       * 1.0 / MAX(measurement_interval, 1)) AS avg_bytes_sent,
                  AVG(bytes_received   * 1.0 / MAX(measurement_interval, 1)) AS avg_bytes_recv,
                  AVG(packets_sent     * 1.0 / MAX(measurement_interval, 1)) AS avg_pkts_sent,
                  AVG(packets_received * 1.0 / MAX(measurement_interval, 1)) AS avg_pkts_recv
            FROM Traffic
            WHERE device_id = ? AND timestamp >= datetime('now', ?)""",
        (device_id, _baseline_window(interval)),
    ).fetchone()

    # too little history to know what "normal" is yet
    if not base or base["samples"] < MIN_BASELINE_SAMPLES:
        return

    # (alert_type, label, current rate, baseline rate, minimum rate, is_bytes, base severity)
    checks = [
        ("bandwidth_spike_out", "Outbound traffic", bytes_sent / interval,
         base["avg_bytes_sent"] or 0, MIN_SPIKE_BYTES_PER_SEC, True, "high"),
        ("bandwidth_spike_in", "Inbound traffic", bytes_received / interval,
         base["avg_bytes_recv"] or 0, MIN_SPIKE_BYTES_PER_SEC, True, "medium"),
        ("packet_spike_out", "Outbound packet rate", packets_sent / interval,
         base["avg_pkts_sent"] or 0, MIN_SPIKE_PKTS_PER_SEC, False, "medium"),
        ("packet_spike_in", "Inbound packet rate", packets_received / interval,
         base["avg_pkts_recv"] or 0, MIN_SPIKE_PKTS_PER_SEC, False, "medium"),
    ]

    for alert_type, label, current, average, minimum, is_bytes, base_severity in checks:
        if average <= 0 or current < minimum:
            continue
        if current < average * SPIKE_THRESHOLD_MULTIPLIER:
            continue

        multiplier = round(current / average, 1)
        severity = base_severity
        if multiplier >= CRITICAL_MULTIPLIER:
            severity = "critical" if base_severity == "high" else "high"

        if is_bytes:
            detail = f"{_fmt_bytes_rate(current)} vs baseline {_fmt_bytes_rate(average)}"
        else:
            detail = f"{current:,.0f} pkts/s vs baseline {average:,.0f} pkts/s"

        _raise_alert(
            cursor, device_id, alert_type, severity,
            f"{label} spike: {detail} ({multiplier}x above average).",
        )


# =============================================================================
# GROUP 4: RECORDING ONE READING
# =============================================================================

def record_device_traffic(device_id, bytes_sent, bytes_received,
                          packets_sent=0, packets_received=0,
                          interval=SAMPLE_INTERVAL, active_count=1, connection=None):
    """Check one device's reading for spikes, then save it to the Traffic table.

    Pass `connection` to share one database connection across many devices
    (the caller then commits); otherwise this opens, commits and closes its own.
    """
    own_connection = connection is None
    if own_connection:
        connection = get_connection()
    cursor = connection.cursor()

    bytes_sent, bytes_received = max(bytes_sent, 0), max(bytes_received, 0)
    packets_sent, packets_received = max(packets_sent, 0), max(packets_received, 0)
    interval = max(int(interval), 1)

    # 1. compare with history (the current reading isn't stored yet)
    _evaluate_device_spike(cursor, device_id, interval,
                           bytes_sent, bytes_received, packets_sent, packets_received)

    # 2. store the reading
    cursor.execute(
        """INSERT INTO Traffic (
                device_id, timestamp, bytes_sent, bytes_received,
                packets_sent, packets_received,
                active_device_count, measurement_interval)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)""",
        (device_id, bytes_sent, bytes_received,
         packets_sent, packets_received, active_count, interval),
    )

    if own_connection:
        connection.commit()
        connection.close()


# =============================================================================
# GROUP 5: THE MONITOR LOOP (started by app/__init__.py)
# =============================================================================

def _take_counters():
    """Copy the four counters and reset them to zero, in one locked step,
    so no packet is counted twice or lost between readings."""
    with _counter_lock:
        snapshot = (dict(_ip_bytes_sent), dict(_ip_bytes_recv),
                    dict(_ip_pkts_sent), dict(_ip_pkts_recv))
        _ip_bytes_sent.clear()
        _ip_bytes_recv.clear()
        _ip_pkts_sent.clear()
        _ip_pkts_recv.clear()
    return snapshot


def _sample_all_devices(elapsed):
    """Record one reading for every active device using the counters' contents."""
    sent_b, recv_b, sent_p, recv_p = _take_counters()

    connection = get_connection()
    try:
        devices = connection.execute(
            "SELECT device_id, device_ip FROM Devices WHERE status = 'active'"
        ).fetchall()

        for device in devices:
            ip = device["device_ip"]
            record_device_traffic(
                device_id=device["device_id"],
                bytes_sent=sent_b.get(ip, 0),
                bytes_received=recv_b.get(ip, 0),
                packets_sent=sent_p.get(ip, 0),
                packets_received=recv_p.get(ip, 0),
                interval=elapsed,
                active_count=len(devices),
                connection=connection,
            )
        connection.commit()
    finally:
        connection.close()


def traffic_monitor_loop():
    """Start the sniffer thread and the sampling thread (both run forever)."""

    threading.Thread(target=_start_packet_sniffer, daemon=True, name="ScapySniffer").start()

    def monitor_loop():
        interval = _read_sample_interval(SAMPLE_INTERVAL)
        last_tick = time.monotonic()
        next_tick = last_tick + interval

        while True:
            # Wait for the next sample in 1-second slices, re-reading the setting
            # each time, so a change on the Settings page applies within about a
            # second instead of after the current (possibly long) interval.
            while True:
                remaining = next_tick - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(1.0, remaining))
                latest = _read_sample_interval(interval)
                if latest != interval:
                    interval = latest
                    next_tick = last_tick + interval

            now = time.monotonic()
            elapsed = max(1, round(now - last_tick))   # real gap, not a fixed number
            last_tick = now
            if now - next_tick > interval:
                # fell far behind (for example the computer slept): skip the
                # catch-up burst, which would log empty samples and drag the
                # baseline down
                next_tick = now
            next_tick += interval

            try:
                _sample_all_devices(elapsed)
            except Exception as err:
                print(f"[Traffic Monitor Error]: {err}")

    threading.Thread(target=monitor_loop, daemon=True, name="TrafficMonitor").start()
