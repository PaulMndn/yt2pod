"""Podcast RSS generation."""

from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ET.register_namespace("itunes", ITUNES)


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


def _duration(seconds):
    if not seconds:
        return None
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def build_feed(sub, episodes, base_url):
    base_url = base_url.rstrip("/")
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = sub["name"]
    ET.SubElement(ch, "link").text = sub["url"]
    ET.SubElement(ch, "description").text = f"Downloaded from {sub['url']} by yt2pod"
    ET.SubElement(ch, "language").text = "en"
    ET.SubElement(ch, _it("author")).text = sub["name"]
    ET.SubElement(ch, _it("explicit")).text = "false"
    image = next((e["thumbnail"] for e in episodes if e["thumbnail"]), None)
    if image:
        ET.SubElement(ch, _it("image"), {"href": image})

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
        if duration := _duration(ep["duration"]):
            ET.SubElement(item, _it("duration")).text = duration
        if ep["thumbnail"]:
            ET.SubElement(item, _it("image"), {"href": ep["thumbnail"]})

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8")
