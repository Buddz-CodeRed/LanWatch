# =============================================================================
# FILE: app/__init__.py  -  THE APP FACTORY (assembles the whole app)
# -----------------------------------------------------------------------------
# Flow:  run.py -> create_app()
#          1. prepare the database        (init_database.py)
#          2. start background workers    (services/network_monitor.py,
#                                          services/scan_scheduler.py,
#                                          services/phone_listener.py)
#          3. register the web pages      (templates/*.html)
#          4. register the API blueprints (routes/*.py)
#        (importing this package also stops tools LanWatch runs from opening
#         console windows: app/no_console.py)
# =============================================================================

import os

# no black console windows flashing up when LanWatch runs netsh / nmap / route
# (Windows; must happen before anything starts a program) - see app/no_console.py
from app.no_console import hide_console_windows
hide_console_windows()

from datetime import timedelta

from flask import Flask, redirect, render_template, request

from app.init_database import init_db, cleanup_interrupted_sessions
from app.services.net_scanner import detect_local_subnets
from app.services.network_monitor import traffic_monitor_loop
from app.services.scan_scheduler import start_scan_scheduler


def create_app():
    """Build and return the LanWatch Flask application."""
    app = Flask(__name__)

    # =========================================================================
    # GROUP 1: DATABASE START-UP
    # Make sure every table exists, then clear scans left "running" last time.
    # =========================================================================
    init_db()
    cleanup_interrupted_sessions()

    # =========================================================================
    # GROUP 1b: SESSION COOKIE (used by the Settings page's admin password lock)
    # The secret key signs the cookie so it can't be forged (app/auth.py).
    # HttpOnly: page scripts can't read it. SameSite=Strict: other websites
    # can't use it to change your settings.
    # =========================================================================
    from app.auth import get_secret_key
    app.secret_key = get_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    # =========================================================================
    # GROUP 2: BACKGROUND WORKERS
    # In debug mode Flask starts the app twice (a file watcher + the real app).
    # WERKZEUG_RUN_MAIN is only "true" in the real one, so the workers start once.
    # =========================================================================
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        traffic_monitor_loop()      # counts traffic per device, raises spike alerts
        start_scan_scheduler()      # runs automatic quick and full scans (Settings page)
        from app.services.phone_listener import start_phone_listener
        start_phone_listener(app)   # home-network address for phones, when switched on

    # =========================================================================
    # GROUP 3: WEB PAGES
    # Each route sends one HTML page. The page's JavaScript then calls the APIs
    # in GROUP 4 to fill itself in.
    # =========================================================================

    # Phones and tablets (any device other than this computer) must pass this
    # gate first: switched on, recognised, signed in (app/phone_access.py).
    # Registered first, so it runs before every other check.
    from app.phone_access import phone_gate
    app.before_request(phone_gate)

    @app.context_processor
    def phone_template_values():
        """base.html shows 'Sign out' in the menu on a signed-in phone."""
        from flask import g
        return {"phone_session": getattr(g, "phone_session", None)}

    @app.before_request
    def require_admin_account():
        """First run: until an admin account exists, every page opens the
        set-up page (the installer normally creates the account already)."""
        path = request.path
        if path.startswith(("/api/", "/static/")) or path in ("/setup", "/favicon.ico"):
            return None
        from app.auth import admin_account
        if admin_account() is None:
            return redirect("/setup")
        return None

    @app.route("/setup")
    def setup_page():
        """First-run set-up: create the admin account (only while none exists)."""
        from app.auth import admin_account
        if admin_account() is not None:
            return redirect("/")
        return render_template("setup.html")

    @app.route("/favicon.ico")
    def favicon():
        """Browsers ask for /favicon.ico on their own: send the LanWatch icon."""
        return app.send_static_file("favicon.svg")

    @app.route("/")
    def home():
        """Dashboard page. Pre-fills the scan box with the best network to scan."""
        return render_template("dashboard.html", local_subnets=_scan_suggestions())

    @app.route("/devices")
    def devices_page():
        """Devices page (details for one device + network summary)."""
        return render_template("devices.html")

    @app.route("/alerts")
    def alerts_page():
        """Security Alerts page."""
        return render_template("alerts.html")

    @app.route("/scans-history")
    def scans_history():
        """Scan History page."""
        return render_template("scans_history.html")

    @app.route("/reports")
    def reports():
        """Reports page."""
        return render_template("reports.html")

    @app.route("/advice")
    def advice_page():
        """Security Advice page (recommendations from full scans)."""
        return render_template("advice.html")

    @app.route("/settings")
    def settings():
        """System Settings page."""
        return render_template("settings.html")

    # =========================================================================
    # GROUP 4: API BLUEPRINTS
    # Each blueprint is one feature's set of /api/... addresses.
    # Imported here (not at the top) to avoid circular imports.
    # =========================================================================
    from app.routes.settings import settings_api        # /api/settings, /api/account
    app.register_blueprint(settings_api)

    from app.routes.auth import auth_api                # /api/auth (settings lock)
    app.register_blueprint(auth_api)

    from app.routes.devices import devices              # /api/devices
    app.register_blueprint(devices)

    from app.routes.scans import scans                  # /api/scans
    app.register_blueprint(scans)

    from app.routes.usage import usage                  # /api/usage
    app.register_blueprint(usage)

    from app.routes.alerts import alerts                # /api/alerts
    app.register_blueprint(alerts)

    from app.routes.local_network import local_network  # /api/network
    app.register_blueprint(local_network)

    from app.routes.findings import findings_api        # /api/findings, /api/knowledge
    app.register_blueprint(findings_api)

    from app.routes.ai import ai_api                    # /api/ai, /api/reports
    app.register_blueprint(ai_api)

    from app.routes.phone import phone                  # /phone-login, /api/phone
    app.register_blueprint(phone)

    from app.routes.backup import backup_api            # /api/backups (Settings -> Backups)
    app.register_blueprint(backup_api)

    return app


# =============================================================================
# GROUP 5: HELPERS
# =============================================================================

def _scan_suggestions():
    """Networks to suggest in the dashboard scan box, best first.

    The "Default network" from the Settings page wins; otherwise the networks
    detected on this computer (net_scanner.detect_local_subnets) are used.
    """
    from app.routes.settings import get_setting

    subnets = detect_local_subnets()
    try:
        default_subnet = get_setting("default_subnet") or ""
    except Exception:
        default_subnet = ""

    if default_subnet:
        subnets = [default_subnet] + [s for s in subnets if s != default_subnet]
    return subnets
