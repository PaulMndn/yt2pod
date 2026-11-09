"""yt2pod: subscribe to YouTube playlists/channels, download them and serve podcast feeds."""

import hmac
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   send_from_directory, url_for)

from . import db
from .downloader import (AUDIO_QUALITIES, NUMBERING, VIDEO_HEIGHTS, clean_folder)
from .feeds import build_feed, format_duration
from .worker import Worker

DEFAULTS = {
    "name": "", "url": "", "mode": "audio", "audio_quality": 192, "video_max_height": "1080",
    "numbering": "oldest_first", "name_prefix": "", "separator": " - ", "folder": "",
    "date_after": None, "max_items": None, "enabled": 1,
}
PUBLIC_ENDPOINTS = {"feed", "media", "static"}


def create_app():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    data_dir = Path(os.environ.get("DATA_DIR", "data")).resolve()
    downloads_dir = data_dir / "downloads"
    for d in (data_dir, downloads_dir):
        d.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or _persistent_secret(data_dir),
        DOWNLOADS_DIR=downloads_dir,
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
    )
    db.init(data_dir / "yt2pod.db")
    worker = Worker(downloads_dir, data_dir / "cookies.txt", int(os.environ.get("CHECK_INTERVAL_MINUTES", "60")))
    app.extensions["yt2pod_worker"] = worker

    def base_url():
        return app.config["PUBLIC_BASE_URL"] or request.url_root.rstrip("/")

    @app.context_processor
    def inject():
        return {"base_url": base_url, "worker": worker, "fmt_date": _fmt_date,
                "fmt_duration": format_duration}

    @app.before_request
    def require_auth():
        password = app.config["ADMIN_PASSWORD"]
        if not password or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        auth = request.authorization
        if auth and auth.password and hmac.compare_digest(auth.password, password):
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="yt2pod"'})

    # --- UI ------------------------------------------------------------------

    @app.get("/")
    def index():
        return render_template("index.html", subs=db.list_subscriptions())

    @app.route("/subscriptions/new", methods=["GET", "POST"])
    def new_subscription():
        form, errors = dict(DEFAULTS), {}
        if request.method == "POST":
            form, errors = _parse_form(request.form)
            if not errors:
                sub_id = db.create_subscription(form)
                worker.enqueue(sub_id)
                flash(f"Added “{form['name']}”. First check has been queued.")
                return redirect(url_for("index"))
        return render_template("edit.html", form=form, errors=errors, sub=None, **_choices())

    @app.route("/subscriptions/<int:sub_id>/edit", methods=["GET", "POST"])
    def edit_subscription(sub_id):
        sub = db.get_subscription(sub_id) or abort(404)
        form, errors = dict(sub), {}
        if request.method == "POST":
            form, errors = _parse_form(request.form, sub_id)
            if not errors:
                db.update_subscription(sub_id, form)
                flash(f"Saved “{form['name']}”. Changes apply to new downloads.")
                return redirect(url_for("index"))
        return render_template("edit.html", form=form, errors=errors, sub=sub, **_choices())

    @app.get("/subscriptions/<int:sub_id>")
    def show_subscription(sub_id):
        sub = db.get_subscription(sub_id) or abort(404)
        return render_template("show.html", sub=sub, episodes=db.list_episodes(sub_id))

    @app.post("/subscriptions/<int:sub_id>/check")
    def check_subscription(sub_id):
        sub = db.get_subscription(sub_id) or abort(404)
        if worker.enqueue(sub_id):
            flash(f"Check of “{sub['name']}” queued.")
        else:
            flash(f"“{sub['name']}” is already queued.")
        return redirect(request.referrer or url_for("index"))

    @app.post("/check-all")
    def check_all():
        worker.enqueue_all()
        flash("All enabled subscriptions queued.")
        return redirect(url_for("index"))

    @app.post("/subscriptions/<int:sub_id>/toggle")
    def toggle_subscription(sub_id):
        sub = db.get_subscription(sub_id) or abort(404)
        db.update_subscription(sub_id, {"enabled": 0 if sub["enabled"] else 1})
        return redirect(url_for("index"))

    @app.post("/subscriptions/<int:sub_id>/delete")
    def delete_subscription(sub_id):
        sub = db.get_subscription(sub_id) or abort(404)
        if worker.is_pending(sub_id):
            flash(f"“{sub['name']}” is being checked right now, try again later.")
            return redirect(url_for("index"))
        removed = 0
        if request.form.get("delete_files"):
            for ep in db.list_episodes(sub_id):
                path = downloads_dir / ep["filepath"]
                if path.is_file():
                    path.unlink()
                    removed += 1
            folder = downloads_dir / sub["folder"]
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        db.delete_subscription(sub_id)
        flash(f"Deleted “{sub['name']}”" + (f" and {removed} file(s)." if removed else "."))
        return redirect(url_for("index"))

    # --- public: feeds and media ---------------------------------------------

    @app.get("/feeds/<int:sub_id>.xml")
    def feed(sub_id):
        sub = db.get_subscription(sub_id) or abort(404)
        xml = build_feed(sub, db.list_episodes(sub_id), base_url())
        return Response(xml, mimetype="application/rss+xml")

    @app.get("/media/<path:filepath>")
    def media(filepath):
        return send_from_directory(downloads_dir, filepath, conditional=True)

    return app


def _choices():
    return {"numbering": NUMBERING, "audio_qualities": AUDIO_QUALITIES, "video_heights": VIDEO_HEIGHTS}


CHOICES = {
    "mode": ("audio", "video"),
    "audio_quality": tuple(str(q) for q in AUDIO_QUALITIES),
    "video_max_height": VIDEO_HEIGHTS,
    "numbering": tuple(NUMBERING),
}
TEXT_FIELDS = ("name", "name_prefix", "separator", "folder")
MAX_TEXT = 100


def _parse_form(f, sub_id=None):
    """Validate the subscription form. Values stay in a form-ready shape (dates as YYYY-MM-DD)."""
    errors = {}
    data = {key: f.get(key, "" if default is None else str(default)) for key, default in DEFAULTS.items()}
    for key in ("name", "url", "folder", "date_after", "max_items"):
        data[key] = data[key].strip()
    data["enabled"] = 1 if f.get("enabled") else 0

    if not data["name"]:
        errors["name"] = "Please give it a name."
    for key in TEXT_FIELDS:
        if len(data[key]) > MAX_TEXT:
            errors[key] = f"At most {MAX_TEXT} characters."

    url = urlparse(data["url"])
    if url.scheme not in ("http", "https") or not url.netloc:
        errors["url"] = "Enter a full URL starting with https://"

    for key, allowed in CHOICES.items():
        if data[key] not in allowed:
            errors[key] = "Please pick one of the options."
    if "audio_quality" not in errors:
        data["audio_quality"] = int(data["audio_quality"])

    data["folder"] = clean_folder(data["folder"] or data["name"])
    if db.folder_in_use(data["folder"], exclude_id=sub_id):
        errors["folder"] = "Another subscription already uses this folder."

    if data["date_after"]:
        try:
            datetime.strptime(data["date_after"], "%Y-%m-%d")
        except ValueError:
            errors["date_after"] = "Invalid date."
    else:
        data["date_after"] = None

    if data["max_items"]:
        if data["max_items"].isdigit() and int(data["max_items"]) > 0:
            data["max_items"] = int(data["max_items"])
        else:
            errors["max_items"] = "Must be a positive number."
    else:
        data["max_items"] = None

    return data, errors


def _fmt_date(value):
    """YYYYMMDD or ISO timestamp -> readable date."""
    if not value:
        return ""
    try:
        if len(value) == 8 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _persistent_secret(data_dir):
    path = data_dir / "secret_key"
    if not path.exists():
        path.write_text(os.urandom(32).hex())
    return path.read_text().strip()
