from yt_dlp import YoutubeDL


URL = "https://www.youtube.com/@28YearsAvatar/videos"
# MODE = sys.argv[1] if len(sys.argv) > 1 else "video"

# if MODE not in {"video", "audio"}:
#     raise SystemExit("Usage: python main.py [video|audio]")


options = {
    "format": (
        "bestaudio[protocol=https]/bestaudio"
    ),
    "outtmpl": "downloads/%(channel)s/%(n_entries+1-playlist_index)d - %(title)s.%(ext)s",
    "noplaylist": False,
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ],
}

with YoutubeDL(options) as downloader:
    downloader.download([URL])
