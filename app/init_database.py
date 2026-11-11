# =============================================================================
# FILE: app/init_database.py  -  DATABASE SET-UP
# -----------------------------------------------------------------------------
# Everything LanWatch learns is stored in one SQLite file: lanwatch.db
#
# Flow:  create_app() (app/__init__.py) calls init_db() and
#        cleanup_interrupted_sessions() once at start-up.
#        Every other file calls get_connection() whenever it needs the database.
# =============================================================================

import sqlite3

from app.paths import data_path

# the database file, in LanWatch's data folder (app/paths.py):
# the project folder while developing, C:\ProgramData\LanWatch when installed.
# Always a full path, so it's found whichever folder LanWatch is started from.
DATABASE = data_path("lanwatch.db")


# =============================================================================
# GROUP 1: CONNECTION HELPER
# Opens the database. Used by every route and service in the app.
# =============================================================================

def get_connection():
    """Open and return a connection to lanwatch.db.

    - row_factory = sqlite3.Row lets code read columns by name: row["device_ip"]
    - PRAGMA foreign_keys = ON makes SQLite enforce links between tables
    The caller must call connection.close() when finished.
    """
    connection = sqlite3.connect(DATABASE)

    # retrieve results by column name
    connection.row_factory = sqlite3.Row

    # enable foreign-key enforcement in sqlite3 (it is off by default)
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


# =============================================================================
# GROUP 2: TABLE CREATION
# Creates every table the first time the app starts. "IF NOT EXISTS" means a
# restart never wipes existing data.
# =============================================================================

def init_db():
    """Create the LanWatch database tables if they do not exist yet."""

    connection = get_connection()
    cursor = connection.cursor()

    # ---------------------------------------------------------
    # Devices - every machine a scan has ever seen
    # Written by: routes/scans.py (_save_scan_results)
    # Read by:    routes/devices.py, services/network_monitor.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Devices (
            device_id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT,
            device_ip TEXT NOT NULL,
            device_mac TEXT UNIQUE,
            device_mac_vendor TEXT,
            os_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'unknown')),
            first_detected DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_detected DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            connected_since DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    # ---------------------------------------------------------
    # Scans - one row per quick/full scan run
    # Written by: routes/scans.py   Read by: routes/scans.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Scans (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_type TEXT NOT NULL CHECK (scan_type IN ('quick', 'full')),
            scan_status TEXT NOT NULL CHECK (scan_status IN ('pending', 'running', 'completed', 'failed')),
            start_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            end_time DATETIME,
            duration_seconds INTEGER,
            network_range TEXT NOT NULL,
            results_summary TEXT,
            CHECK (end_time IS NULL OR end_time >= start_time))""")

    # ---------------------------------------------------------
    # ScanMap - links Scans and Devices ("scan 7 found device 3")
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ScanMap (
            scan_map_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            device_id INTEGER NOT NULL,
            device_status TEXT NOT NULL,
            detection_status TEXT NOT NULL,
            FOREIGN KEY (scan_id) REFERENCES Scans(scan_id) ON DELETE CASCADE,
            FOREIGN KEY (device_id) REFERENCES Devices(device_id) ON DELETE CASCADE)""")

    # ---------------------------------------------------------
    # DevicePorts - open ports and services found by FULL scans
    # ("full scan 9 found port 443 (https, nginx 1.24) open on device 3")
    # A device's current open ports = the rows from its latest full scan.
    # Written by: routes/scans.py (_save_scan_results)
    # Read by:    routes/devices.py (open_ports in GET /api/devices/)
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS DevicePorts (
            port_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            device_id INTEGER NOT NULL,
            port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
            protocol TEXT NOT NULL CHECK (protocol IN ('tcp', 'udp')),
            service TEXT,
            product TEXT,
            version TEXT,
            FOREIGN KEY (scan_id) REFERENCES Scans(scan_id) ON DELETE CASCADE,
            FOREIGN KEY (device_id) REFERENCES Devices(device_id) ON DELETE CASCADE)""")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_deviceports_device_scan
        ON DevicePorts (device_id, scan_id)""")

    # ---------------------------------------------------------
    # Finding - security advice raised by the rules in services/findings.py
    # ("Telnet is open on the printer -> article disable_telnet").
    # One row per device + rule; status: open -> done / dismissed.
    # Written by: services/findings.py (after every full scan),
    #             routes/findings.py (user marks done / not relevant)
    # Read by:    routes/findings.py (Security Advice page), routes/ai.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Finding (
            finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
            title TEXT NOT NULL,
            detail TEXT,
            article_id TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dismissed')),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (device_id, rule_id),
            FOREIGN KEY (device_id) REFERENCES Devices(device_id) ON DELETE CASCADE)""")

    # ---------------------------------------------------------
    # Traffic - one reading per device every few seconds
    # Written by: services/network_monitor.py
    # Read by:    routes/devices.py, routes/usage.py, the spike detector
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Traffic (
            traffic_id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL,
            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bytes_sent INTEGER NOT NULL,
            bytes_received INTEGER NOT NULL,
            packets_sent INTEGER NOT NULL,
            packets_received INTEGER NOT NULL,
            active_device_count INTEGER NOT NULL,
            measurement_interval INTEGER NOT NULL,
            FOREIGN KEY (device_id) REFERENCES Devices(device_id) ON DELETE CASCADE)""")

    # ---------------------------------------------------------
    # Baseline - saved view: all-time average traffic per device
    # (kept for future reports; the spike detector works out its own
    #  recent average in services/network_monitor.py)
    # ---------------------------------------------------------
    cursor.execute("DROP VIEW IF EXISTS Baseline")
    cursor.execute("""
        CREATE VIEW Baseline AS
        SELECT
            device_id,
            ROUND(AVG(bytes_sent), 2) AS average_bytes_sent,
            ROUND(AVG(bytes_received), 2) AS average_bytes_received,
            ROUND(AVG(packets_sent), 2) AS average_packets_sent,
            ROUND(AVG(packets_received), 2) AS average_packets_received,
            COUNT(*) AS total,
            MAX(timestamp) AS last_updated
        FROM Traffic
        GROUP BY device_id""")

    # ---------------------------------------------------------
    # Alert - traffic spike warnings
    # Written by: services/network_monitor.py   Read by: routes/alerts.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Alert (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
            message TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_resolved BOOLEAN NOT NULL DEFAULT 0,
            resolved_at DATETIME,
            FOREIGN KEY (device_id) REFERENCES Devices(device_id) ON DELETE SET NULL)""")

    # ---------------------------------------------------------
    # Report - the network summary written after each full scan
    # (tech_summary = the facts as plain text; ai_translation_summary = the
    #  AI's plain-English version, empty if the AI is off or unavailable)
    # Written by: services/scan_summary.py   Read by: routes/ai.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Report (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER,
            tech_summary TEXT NOT NULL,
            ai_translation_summary TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scan_id) REFERENCES Scans(scan_id) ON DELETE CASCADE)""")

    # ---------------------------------------------------------
    # Account - the admin login (password stored as a one-way hash)
    # Written/read by: routes/settings.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Account (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    # ---------------------------------------------------------
    # System_Config - Settings page values (one key/value row per setting)
    # Written by: routes/settings.py
    # Read by:    routes/settings.py, services/network_monitor.py,
    #             services/scan_scheduler.py, app/__init__.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS System_Config (
            config_id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    # ---------------------------------------------------------
    # PhoneSession - phones and tablets signed in over the home network
    # Only a one-way hash of each sign-in token is stored, so reading the
    # database doesn't let anyone sign in. Signed-in for 30 days of no use.
    # Written and read by: app/phone_access.py
    # ---------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS PhoneSession (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            device_label TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    # ---------------------------------------------------------
    # Columns added after the first version. CREATE TABLE IF NOT EXISTS never
    # changes an existing table, so they are added here if missing; existing
    # data is kept.
    # ---------------------------------------------------------
    # Devices.custom_name - a name the user typed (Devices page -> Rename).
    # Scans never change it; pages show it instead of the automatic name.
    _add_column_if_missing(cursor, "Devices", "custom_name", "TEXT")
    # Devices.name_source - where the automatic name came from (netbios, dns,
    # mdns or fallback), so a better name is never replaced by a worse one
    _add_column_if_missing(cursor, "Devices", "name_source", "TEXT")

    # Devices.recognised / recognised_by / recognised_at - is this one of the
    # household's devices, and who decided ("learning" period, "you" on the
    # Devices page, or "automatic" after a week): services/change_detection.py.
    # Devices that existed before this feature count as recognised.
    if _add_column_if_missing(cursor, "Devices", "recognised", "INTEGER NOT NULL DEFAULT 0"):
        cursor.execute("UPDATE Devices SET recognised = 1")
    _add_column_if_missing(cursor, "Devices", "recognised_by", "TEXT")
    _add_column_if_missing(cursor, "Devices", "recognised_at", "DATETIME")
    cursor.execute("UPDATE Devices SET recognised_by = 'learning' WHERE recognised = 1 AND recognised_by IS NULL")

    # save the changes and close the set-up connection
    connection.commit()
    connection.close()


def _add_column_if_missing(cursor, table, column, definition):
    """Add a column to an existing table (a tiny "migration")."""
    columns = [row[1] for row in cursor.execute(f"PRAGMA table_info({table})")]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        return True                       # added just now
    return False


# =============================================================================
# GROUP 3: START-UP CLEAN-UP
# Fixes leftovers from the last time the app was closed.
# =============================================================================

def cleanup_interrupted_sessions():
    """Mark scans left 'pending' or 'running' as failed.

    If the server stopped in the middle of a scan, its row would otherwise say
    "running" forever and block every new scan.
    """
    connection = get_connection()
    connection.execute("""
        UPDATE Scans
        SET scan_status = 'failed',
            end_time = CURRENT_TIMESTAMP,
            results_summary = 'scan interrupted by server restart'
        WHERE scan_status IN ('pending', 'running')""")
    connection.commit()
    connection.close()
