"""yt2pod: subscribe to YouTube playlists/channels, download them and serve podcast feeds."""

import hmac
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   send_from_directory, url_for)

from . import db
from .downloader import (AUDIO_QUALITIES, NUMBERING, VIDEO_HEIGHTS, clean_folder)
from .feeds import build_feed
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
    archives_dir = data_dir / "archives"
    for d in (data_dir, downloads_dir, archives_dir):
        d.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or _persistent_secret(data_dir),
        DOWNLOADS_DIR=downloads_dir,
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
    )
    db.init(data_dir / "yt2pod.db")
    worker = Worker(downloads_dir, archives_dir, int(os.environ.get("CHECK_INTERVAL_MINUTES", "60")))
    app.extensions["yt2pod_worker"] = worker

    def base_url():
        return app.config["PUBLIC_BASE_URL"] or request.url_root.rstrip("/")

    @app.context_processor
    def inject():
        return {"base_url": base_url, "worker": worker, "fmt_date": _fmt_date,
                "fmt_duration": _fmt_duration}

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
            form, errors = _parse_form(request.form)
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
        (archives_dir / f"{sub_id}.txt").unlink(missing_ok=True)
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


def _parse_form(f):
    errors = {}
    data = {
        "name": f.get("name", "").strip(),
        "url": f.get("url", "").strip(),
        "mode": f.get("mode", "audio"),
        "audio_quality": f.get("audio_quality", "192"),
        "video_max_height": f.get("video_max_height", "1080"),
        "numbering": f.get("numbering", "oldest_first"),
        "name_prefix": f.get("name_prefix", ""),
        "separator": f.get("separator", ""),
        "folder": f.get("folder", "").strip(),
        "date_after": f.get("date_after", "").strip() or None,
        "max_items": f.get("max_items", "").strip() or None,
        "enabled": 1 if f.get("enabled") else 0,
    }
    if not data["name"]:
        errors["name"] = "Please give it a name."
    if not re.match(r"https?://", data["url"]):
        errors["url"] = "Enter a full URL starting with https://"
    if data["mode"] not in ("audio", "video"):
        errors["mode"] = "Invalid mode."
    try:
        data["audio_quality"] = int(data["audio_quality"])
        assert data["audio_quality"] in AUDIO_QUALITIES
    except (ValueError, AssertionError):
        errors["audio_quality"] = "Invalid quality."
    if data["video_max_height"] not in VIDEO_HEIGHTS:
        errors["video_max_height"] = "Invalid resolution."
    if data["numbering"] not in NUMBERING:
        errors["numbering"] = "Invalid numbering."
    data["folder"] = clean_folder(data["folder"] or data["name"])
    if data["date_after"]:
        try:  # from <input type=date> (YYYY-MM-DD) to yt-dlp's YYYYMMDD
            data["date_after"] = datetime.strptime(data["date_after"], "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            errors["date_after"] = "Invalid date."
    if data["max_items"]:
        try:
            data["max_items"] = int(data["max_items"])
            assert data["max_items"] > 0
        except (ValueError, AssertionError):
            errors["max_items"] = "Must be a positive number."
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


def _fmt_duration(seconds):
    if not seconds:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _persistent_secret(data_dir):
    path = data_dir / "secret_key"
    if not path.exists():
        path.write_text(os.urandom(32).hex())
    return path.read_text().strip()
