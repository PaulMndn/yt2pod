"""Podcast RSS generation."""

from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ET.register_namespace("itunes", ITUNES)

# Feed languages offered in the form (ISO 639-1 codes, as used in RSS <language>).
LANGUAGES = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
    "nl": "Dutch", "pt": "Portuguese", "pl": "Polish", "cs": "Czech", "da": "Danish",
    "sv": "Swedish", "no": "Norwegian", "fi": "Finnish", "tr": "Turkish", "ru": "Russian",
    "uk": "Ukrainian", "ar": "Arabic", "hi": "Hindi", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese",
}


def _it(tag):
    return f"{{{ITUNES}}}{tag}"


def _pub_date(ep):
    raw = ep["upload_date"]
    if raw:
        try:
            dt = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
            return format_datetime(dt)
        except ValueError:
            pass
    return format_datetime(datetime.fromisoformat(ep["downloaded_at"]))


def format_duration(seconds):
    """H:MM:SS, or M:SS under an hour (both valid for itunes:duration); "" if unknown."""
    if not seconds:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_feed(sub, episodes, base_url):
    base_url = base_url.rstrip("/")
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = sub["name"]
    ET.SubElement(ch, "link").text = sub["url"]
    ET.SubElement(ch, "description").text = (
        sub["feed_description"] or f"Downloaded from {sub['url']} by yt2pod")
    ET.SubElement(ch, "language").text = sub["language"]
    ET.SubElement(ch, _it("author")).text = sub["feed_author"] or sub["name"]
    ET.SubElement(ch, _it("explicit")).text = "false"
    # Cover: channel profile picture / playlist image, else the newest episode's thumbnail.
    image = sub["image_url"] or next((e["thumbnail"] for e in episodes if e["thumbnail"]), None)
    if image:
        ET.SubElement(ch, _it("image"), {"href": image})
        rss_image = ET.SubElement(ch, "image")  # plain RSS readers use this one
        ET.SubElement(rss_image, "url").text = image
        ET.SubElement(rss_image, "title").text = sub["name"]
        ET.SubElement(rss_image, "link").text = sub["url"]

    for ep in episodes:
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = ep["description"] or ""
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = ep["video_id"]
        ET.SubElement(item, "pubDate").text = _pub_date(ep)
        if ep["webpage_url"]:
            ET.SubElement(item, "link").text = ep["webpage_url"]
        ET.SubElement(item, "enclosure", {
            "url": f"{base_url}/media/{quote(ep['filepath'])}",
            "length": str(ep["filesize"] or 0),
            "type": ep["mime"] or "application/octet-stream",
        })
        if duration := format_duration(ep["duration"]):
            ET.SubElement(item, _it("duration")).text = duration
        if ep["thumbnail"]:
            ET.SubElement(item, _it("image"), {"href": ep["thumbnail"]})

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8")
