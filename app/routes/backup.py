# =============================================================================
# FILE: app/routes/backup.py  -  BACKUPS API  (/api/backups)
# -----------------------------------------------------------------------------
# The "Backups" section of the Settings page (js/backups.js). The work itself
# is done in services/backup.py.
#
#   GET  /api/backups/                 the list of backups (newest first)
#   POST /api/backups/                 "Back up now"
#   POST /api/backups/upload           "Add a backup file" (checked, then listed)
#   POST /api/backups/<name>/restore   "Restore" (current data is backed up first)
#   GET  /api/backups/<name>/download  save a copy somewhere else (USB stick...)
#
# Access control: every route needs the Settings page unlocked with the admin
# password (@settings_unlock_required, app/auth.py). After a restore every
# browser must unlock again: the restored data may hold a different password.
# =============================================================================

from flask import Blueprint, jsonify, request, send_file

from app.auth import settings_unlock_required
from app.services import backup as backups

backup_api = Blueprint("backup_api", __name__, url_prefix="/api/backups")


def _listing(**extra):
    return jsonify(backups=backups.list_backups(), folder=backups.backup_dir(),
                   keep=backups.KEEP_BACKUPS, **extra)


@backup_api.route("/", methods=["GET"])
@settings_unlock_required
def list_all():
    return _listing()


@backup_api.route("/", methods=["POST"])
@settings_unlock_required
def back_up_now():
    try:
        name = backups.backup_now()
    except backups.BackupError as err:
        return jsonify(error=str(err)), 500
    return _listing(created=name), 201


@backup_api.route("/upload", methods=["POST"])
@settings_unlock_required
def upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(error="Choose a backup file first."), 400
    if request.content_length and request.content_length > backups.MAX_UPLOAD_BYTES:
        return jsonify(error="That file is far too big to be a LanWatch backup."), 413
    try:
        name = backups.save_uploaded(file.stream)
    except backups.BackupError as err:
        return jsonify(error=str(err)), 400
    return _listing(created=name), 201


@backup_api.route("/<name>/restore", methods=["POST"])
@settings_unlock_required
def restore(name):
    try:
        safety = backups.restore_backup(name)
    except backups.BackupError as err:
        return jsonify(error=str(err)), 409
    # the restored data may have a different admin password: everyone unlocks again
    from app import auth
    with auth._tickets_lock:
        auth._tickets.clear()
    # phones waiting to be let in were listed by the old data's device numbers
    from app import phone_access
    with phone_access._waiting_lock:
        phone_access._waiting.clear()
    return jsonify(restored=name, safety_copy=safety)


@backup_api.route("/<name>/download", methods=["GET"])
@settings_unlock_required
def download(name):
    try:
        path = backups.path_for(name)
    except backups.BackupError as err:
        return jsonify(error=str(err)), 404
    return send_file(path, as_attachment=True, download_name=name,
                     mimetype="application/vnd.sqlite3", max_age=0)
