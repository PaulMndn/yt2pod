"""Translate user-friendly subscription settings into yt-dlp options and run downloads."""

import logging
import mimetypes
import re
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DateRange

from . import db

log = logging.getLogger(__name__)

NUMBERING = {
    # key: (label, yt-dlp template fragment, example)
    "oldest_first": ("Oldest video = 1 (for channels)", "%(n_entries+1-playlist_index)03d", "007"),
    "playlist_order": ("Playlist position", "%(playlist_index)03d", "007"),
    "date": ("Upload date", "%(upload_date>%Y-%m-%d)s", "2026-01-31"),
    "none": ("No number", "", ""),
}
AUDIO_QUALITIES = (128, 192, 320)
VIDEO_HEIGHTS = ("480", "720", "1080", "best")
MIME_OVERRIDES = {".m4a": "audio/mp4", ".opus": "audio/ogg", ".mp3": "audio/mpeg", ".mp4": "video/mp4"}

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def clean_text(value):
    """Strip characters that are unsafe in file names."""
    return _UNSAFE.sub("", value or "")


def clean_folder(value):
    """Folder name relative to the downloads dir: one path segment, no traversal."""
    value = clean_text(value).strip().strip(".")
    return value or "untitled"


def filename_template(sub):
    number = NUMBERING[sub["numbering"]][1]
    prefix = clean_text(sub["name_prefix"]).replace("%", "%%")
    separator = clean_text(sub["separator"]).replace("%", "%%")
    head = f"{prefix}{number}{separator}" if number else prefix
    return f"{head}%(title)s.%(ext)s"


def build_options(sub, downloads_dir, cookies_file=None):
    opts = {
        "outtmpl": str(Path(downloads_dir) / sub["folder"] / filename_template(sub)),
        "noplaylist": False,
        "ignoreerrors": True,
        "download_archive": _EpisodeArchive(sub["id"]),
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": _CollectingLogger(),
        # YouTube needs a JS runtime; deno is in the Docker image, node is a local fallback.
        "js_runtimes": {"deno": {}, "node": {}},
    }

    # Optional Netscape-format cookies (helps against YouTube's "confirm you're not a bot").
    if cookies_file and Path(cookies_file).is_file():
        opts["cookiefile"] = str(cookies_file)

    if sub["mode"] == "audio":
        opts["format"] = "bestaudio[protocol=https]/bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(sub["audio_quality"]),
        }]
    else:
        h = sub["video_max_height"]
        if h == "best":
            opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        else:
            opts["format"] = (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                              f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best")
        opts["merge_output_format"] = "mp4"

    if sub["date_after"]:
        opts["daterange"] = DateRange(sub["date_after"].replace("-", ""))  # YYYY-MM-DD -> YYYYMMDD

    if sub["max_items"]:
        # Filter by position instead of using `playlistend`, so that n_entries (used for
        # "oldest = 1" numbering) still reflects the whole list and numbers stay stable.
        limit = int(sub["max_items"])

        def match_filter(info, *, incomplete=False):
            index = info.get("playlist_index")
            if index is not None and index > limit:
                return f"beyond the first {limit} entries"
            return None

        opts["match_filter"] = match_filter

    return opts


class _EpisodeArchive:
    """yt-dlp download archive backed by the episodes table instead of a text file.

    yt-dlp only needs `in` and `add()`. Entries look like "youtube <video_id>"; we match on the
    video id. `add()` is a no-op because _RecordEpisode stores the episode after the move.
    """

    def __init__(self, sub_id):
        self.sub_id = sub_id

    def __bool__(self):  # yt-dlp skips the lookup entirely for a falsy archive
        return True

    def __contains__(self, archive_id):
        # yt-dlp may ask with None for entries it can't build an id for yet
        return bool(archive_id) and db.has_episode(self.sub_id, archive_id.rpartition(" ")[2])

    def add(self, archive_id):
        pass


class _CollectingLogger:
    def __init__(self):
        self.errors = []

    # Per-item skip messages flood the log on big channels; keep them at debug level.
    _NOISY = ("[debug] ", "beyond the first", "has already been recorded in the archive",
              "upload date is not in range")

    def debug(self, msg):
        level = logging.DEBUG if any(n in msg for n in self._NOISY) else logging.INFO
        log.log(level, msg)

    def info(self, msg):
        log.info(msg)

    def warning(self, msg):
        log.warning(msg)

    def error(self, msg):
        log.error(msg)
        self.errors.append(msg)


class _RecordEpisode(PostProcessor):
    """Runs after the final file is in place and stores it as an episode."""

    def __init__(self, sub_id, downloads_dir):
        super().__init__()
        self.sub_id = sub_id
        self.downloads_dir = Path(downloads_dir).resolve()
        self.count = 0

    def run(self, info):
        path = Path(info["filepath"]).resolve()
        mime = MIME_OVERRIDES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
        db.add_episode(self.sub_id, {
            "video_id": info["id"],
            "title": info.get("title") or info["id"],
            "description": info.get("description"),
            "upload_date": info.get("upload_date"),
            "duration": int(info["duration"]) if info.get("duration") else None,
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url"),
            "filepath": path.relative_to(self.downloads_dir).as_posix(),
            "filesize": path.stat().st_size if path.exists() else None,
            "mime": mime or "application/octet-stream",
        })
        self.count += 1
        return [], info


def run_subscription(sub_id, downloads_dir, cookies_file=None):
    """Check one subscription for new videos and download them. Returns number of new episodes."""
    sub = db.get_subscription(sub_id)
    if sub is None:
        return 0
    db.set_status(sub_id, "running")
    opts = build_options(sub, downloads_dir, cookies_file)
    recorder = _RecordEpisode(sub_id, downloads_dir)
    try:
        with YoutubeDL(opts) as ydl:
            ydl.add_post_processor(recorder, when="after_move")
            ydl.download([sub["url"]])
    except Exception as exc:  # noqa: BLE001 - report anything to the UI
        log.exception("Subscription %s failed", sub_id)
        db.set_status(sub_id, "error", str(exc))
        return recorder.count

    errors = opts["logger"].errors
    if errors:
        status = f"{recorder.count} new, {len(errors)} error(s)"
        db.set_status(sub_id, status, "\n".join(errors[-5:]))
    else:
        db.set_status(sub_id, f"ok, {recorder.count} new")
    return recorder.count
