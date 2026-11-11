# =============================================================================
# FILE: run.py  -  THE START BUTTON
# -----------------------------------------------------------------------------
# Run with:  python run.py   (from an Administrator PowerShell, see BUILD_GUIDE.md)
#
# Flow:  python run.py -> create_app() in app/__init__.py builds the whole app
#        -> app.run() starts the web server at http://127.0.0.1:5000
# =============================================================================

from app import create_app

# build the Flask application (database, background workers, pages and APIs)
app = create_app()

if __name__ == "__main__":
    # start the Flask development server
    # debug=True: auto-restart when a file is saved + detailed error pages
    app.run(debug=True)
