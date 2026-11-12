> This is a vibe-coded project. While I have deployed the app for myself, I have not yet fully reviewed the code.

# yt2pod

A small self-hosted web app that turns YouTube playlists and channels into podcast feeds.
You add a playlist or channel URL in the browser and pick a few settings. yt2pod then checks
it regularly, downloads new videos as mp3 (or mp4) and serves an RSS feed for each subscription
that you can add to any podcast app.

- Web UI to add, edit, pause, delete and manually check subscriptions
- Simple download settings: audio/video, quality, file numbering, prefix, date and count limits
- Downloads with [yt-dlp](https://github.com/yt-dlp/yt-dlp); each video is downloaded once
- Podcast RSS feed per subscription, including the audio/video files
- Everything stored in one SQLite file plus a downloads folder
- Runs as a single Docker container

---

## Contents

- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Using the app](#using-the-app)
- [How it works](#how-it-works)
- [Development](#development)
- [Versioning and releases](#versioning-and-releases)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)

---

## Quick start

With Docker:

```bash
make deploy
```

Then open http://localhost:8000. Data is stored in `./data`.

Without Docker, you need Python 3.12+ and ffmpeg (see [Development](#development)):

```bash
make dev
```

---

## Configuration

All configuration is done through environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `data` (`/data` in Docker) | Where the database, downloads and secret key are stored. |
| `PUBLIC_BASE_URL` | the address of the incoming request | Address used for links in RSS feeds and the "Copy feed URL" button, e.g. `https://pod.example.com`. **Set this whenever the app runs behind a reverse proxy or domain.** |
| `CHECK_INTERVAL_MINUTES` | `60` | How often all enabled subscriptions are checked. |
| `ADMIN_PASSWORD` | empty (no login) | If set, the web UI asks for this password (HTTP basic auth, any username). Feeds and media stay public so podcast apps can fetch them. |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | Where the web server listens. |
| `THREADS` | `1` | Number of web server threads (minimum 1). One is enough: file downloads don't block it, and YouTube downloads run in a separate background thread. |
| `LOG_LEVEL` | `INFO` | Python log level. `DEBUG` also shows skipped videos. |
| `SECRET_KEY` | generated and saved in `DATA_DIR/secret_key` | Only used to sign the UI's confirmation messages. |
| `YT2POD_VERSION` | `dev` | Version shown in the footer and startup log. Set at build time, see [Versioning](#versioning-and-releases). |

Optional: if `DATA_DIR/cookies.txt` exists (Netscape cookie format), yt-dlp uses it. This can
help if YouTube starts asking to "confirm you're not a bot".

---

## Using the app

### Adding a subscription

Click **+ Add subscription** and fill in:

- **Name** and **URL**: a playlist URL (`…/playlist?list=…`) or a channel's videos tab
  (`https://www.youtube.com/@channel/videos`). Other sites supported by yt-dlp also work.
- **Format**: audio only (mp3 at 128/192/320 kbit/s) or video (mp4, max resolution 480p–best).
- **File names**: `folder/` + *prefix* + *number* + *separator* + video title. The form shows
  an example as you type, e.g. `Avatar/Ep007 - Title.mp3`.

  | Number option | Result | Notes |
  |---|---|---|
  | Oldest video = 1 | `007` | For channels, which list the newest video first. |
  | Playlist position | `007` | For playlists, which usually list the oldest first. |
  | Upload date | `2026-01-31` | |
  | No number | – | The separator is dropped too. |

- **Limits** (optional):
  - *Only videos uploaded on or after* a date.
  - *Only the first N entries of the list*. On a channel tab these are the N newest videos.
    Numbering still counts the whole list, so numbers stay stable as the channel grows.
- **Feed language**: the language podcast apps show for the feed (default English).
- **Check automatically**: if unchecked, the subscription is only checked manually.

Each folder can belong to only one subscription. Changing settings later only affects new
downloads; existing files are not renamed.

### Feeds

Every subscription card shows its feed URL (`/feeds/<id>.xml`) with a copy button. Add that URL
to your podcast app. The feed links each episode to `/media/<folder>/<file>`. Both URLs must be
reachable from the device running the podcast app, so set `PUBLIC_BASE_URL` accordingly.

The feed's cover, description and author are taken from YouTube: the channel's profile picture,
description and name, or the playlist's image, description and owner. They are picked up during each
check, so a new subscription shows them after its first check. Until then, the feed uses the newest
episode's thumbnail, a generic description and the subscription name.

### Checking and status

Subscriptions are checked every `CHECK_INTERVAL_MINUTES` and when you click **Check now** /
**Check all now**. The card shows whether a check is queued or running, the time of the last
check, the number of new episodes and, if something went wrong, the last few errors.

---

## How it works

### Project layout

```
yt2pod/
  __init__.py      Flask app: configuration, routes, form validation
  __main__.py      Entry point (`python -m yt2pod`): starts the worker and the waitress server
  db.py            SQLite schema and queries
  downloader.py    Turns subscription settings into yt-dlp options and runs a download
  worker.py        Background thread: download queue and periodic schedule
  feeds.py         RSS feed generation
  templates/       Jinja templates (overview, edit form, episode list)
  static/style.css Styles (light and dark mode)
Dockerfile         Image with Python, ffmpeg, deno and the app
docker-compose.yml Builds and runs the image for development and local use
Makefile           Shortcuts for building, deploying and releasing
```

### Data directory

```
$DATA_DIR/
  yt2pod.db        SQLite database (subscriptions and episodes)
  downloads/       Downloaded files, one folder per subscription
  secret_key       Generated on first start
  cookies.txt      Optional, provided by you
```

Back up this folder to back up everything.

### Database

Two tables, created on startup if missing (see `db.py`). Columns added in later versions are
listed in `ADDED_COLUMNS` and added to existing databases on startup.

- `subscriptions`: URL, name and all settings from the form, plus `enabled` and the result of
  the last check (`last_checked`, `last_status`, `last_error`), the feed language and what
  yt-dlp reports about the channel or playlist (`image_url`, `feed_description`, `feed_author`).
- `episodes`: one row per downloaded video: video ID, title, description, upload date,
  duration, thumbnail, file path relative to `downloads/`, size and MIME type. Unique per
  `(subscription_id, video_id)`.

Each web request thread and the worker thread use their own SQLite connection, with WAL mode
enabled so reading and writing at the same time doesn't block.

### What happens during a check

1. The worker takes a subscription ID from its queue (`worker.py`). There is a single worker
   thread, so downloads run one at a time, which also keeps the request rate to YouTube low.
   The same thread queues all enabled subscriptions every `CHECK_INTERVAL_MINUTES`; the first
   scheduled run is 10 seconds after startup. A subscription that is already queued is not
   queued twice.
2. `downloader.build_options()` turns the subscription's settings into yt-dlp options: format,
   mp3 conversion, output file name template, date range and item limit.
3. yt-dlp lists the playlist or channel. For every entry it asks the **download archive**
   whether the video is already downloaded. yt2pod's archive (`_EpisodeArchive`) answers by
   looking the video up in the `episodes` table, so already downloaded videos are skipped
   without being fetched.
4. New videos are downloaded and converted. Once the final file is in place, a yt-dlp
   post-processor (`_RecordEpisode`) saves the episode to the database.
   After the whole list is processed, a second post-processor (`_RecordChannelInfo`) stores the
   channel's or playlist's description, author and cover. For the cover it picks the channel's
   profile picture (`avatar_uncropped`), otherwise the largest square image, otherwise the largest
   image.
5. The subscription's status is updated, e.g. `ok, 2 new` or `1 new, 1 error(s)` with the
   error messages.

The "oldest = 1" numbering uses yt-dlp's `n_entries` (the number of entries in the list). The
"first N entries" limit is therefore implemented as a filter on the playlist position rather than
yt-dlp's `playlistend`, which would change `n_entries` and break the numbering.

### Web routes

| Route | Purpose |
|---|---|
| `GET /` | Overview of all subscriptions |
| `GET/POST /subscriptions/new` | Add form |
| `GET/POST /subscriptions/<id>/edit` | Edit form |
| `GET /subscriptions/<id>` | Episode list |
| `POST /subscriptions/<id>/check`, `POST /check-all` | Queue checks |
| `POST /subscriptions/<id>/toggle` | Pause / resume automatic checks |
| `POST /subscriptions/<id>/delete` | Delete, optionally with the downloaded files |
| `GET /feeds/<id>.xml` | RSS feed (public) |
| `GET /media/<path>` | Downloaded files, with range request support (public) |

When `ADMIN_PASSWORD` is set, everything except feeds, media and static files requires login.

---

## Development

### Setup

You need Python 3.12 or newer and `ffmpeg`. A JavaScript runtime (`deno` or `node`) is also
needed: yt-dlp uses it to download from YouTube. Either works locally; the Docker image uses deno.

This repository uses a conda environment in `.env/` (ignored by git), which provides Python,
ffmpeg and node:

```bash
.env/bin/pip install -r requirements.txt
```

Any other virtualenv works too, as long as `ffmpeg` and `deno` or `node` are on the `PATH`.

> **Note:** `.env` here is a directory, not the usual environment-variable file. Docker
> Compose would normally read a `.env` file in this folder; that's why the development setup
> passes variables through the shell and the Makefile instead.

### Running locally

```bash
make dev
```

This starts the app on http://localhost:8000 with data in `./data`, using `.env/bin/python`.
The footer shows version `dev`. Without the Makefile:

```bash
DATA_DIR=./data python -m yt2pod
```

Use e.g. `CHECK_INTERVAL_MINUTES=600` while developing to avoid automatic checks, and
`LOG_LEVEL=DEBUG` to see what yt-dlp skips and why.

### Notes for working on the code

- **yt-dlp changes often.** YouTube downloads break from time to time until yt-dlp is updated.
  `requirements.txt` deliberately doesn't pin yt-dlp, so rebuilding the image gets the newest
  version.
- **Pylance:** the `YoutubeDL(opts)` call has a `pyright: ignore` comment. The yt-dlp type stubs
  bundled with Pylance declare `daterange` and `download_archive` as strings, but yt-dlp expects
  a `DateRange` object and accepts any container for the archive.
- **Schema changes:** the database is created with `CREATE TABLE IF NOT EXISTS`, which does not
  change existing tables. To add a column, add it to `SCHEMA` (for new databases) and to
  `ADDED_COLUMNS` in `db.py` (for existing ones). Only nullable columns or columns with a
  default work this way; anything else (renames, type changes) needs a manual migration.
- **No tests yet.** Quick manual check: add a subscription with *first N entries* set to 1, click
  **Check now**, then check that the file appears in `data/downloads/`, a second check reports
  `ok, 0 new`, and `/feeds/<id>.xml` lists the episode.

---

## Versioning and releases

Releases are git tags such as `v0.1.0`. Each Docker image is tagged with the version it was built
from, e.g. `yt2pod:v0.1.0`, and the running app shows it in the footer and startup log.

### Makefile targets

| Command | What it does |
|---|---|
| `make build` | Build the image `yt2pod:<version>` from the current working directory. |
| `make deploy` | `build`, then start or replace the container with `docker compose up -d`. |
| `make rollback VERSION=v0.1.0` | Start an image that was built earlier, without rebuilding. |
| `make logs` | Follow the container logs. |
| `make release VERSION=v0.2.0` | Tag the current commit. Refuses without an explicit `VERSION` or with uncommitted changes to tracked files. Does not push. |
| `make dev` | Run the app locally without Docker. |

Unless you pass `VERSION=…`, the version comes from `git describe --tags --always --dirty`:

| State of the working directory | Version |
|---|---|
| exactly on tag `v0.1.0`, no changes | `v0.1.0` |
| 3 commits after `v0.1.0` | `v0.1.0-3-gabc1234` |
| no tags yet | the commit hash, e.g. `19d3373` |
| uncommitted changes | `…-dirty` |

### How the version gets into the app

```
make … VERSION=v0.1.0   (or git describe)
  → Makefile exports YT2POD_VERSION
  → docker-compose.yml: build argument VERSION and image name yt2pod:v0.1.0
  → Dockerfile: ARG VERSION → LABEL org.opencontainers.image.version and ENV YT2POD_VERSION
  → app reads YT2POD_VERSION
```

The version is stored inside the image at build time, so an image always reports the version it
was built from, regardless of the environment it is started in.

### Typical release

```bash
git status                       # everything committed?
make release VERSION=v0.2.0      # tag the commit
make build                       # builds yt2pod:v0.2.0
git push && git push --tags      # optional, if you use a remote
```

The image is always built from the files in the working directory, uncommitted changes included,
and labelled with whatever `VERSION` you pass. To build exactly what a tag contains, regardless
of the working directory:

```bash
git archive v0.2.0 | docker build -t yt2pod:v0.2.0 --build-arg VERSION=v0.2.0 -
```

---

## Deployment

The `docker-compose.yml` in this repository is meant for development and local use: it builds
the image from source. For production, use a separate folder with a compose file that only runs
already built images, and a `.env` file that says which version to run.

### Production folder

```
/srv/yt2pod/
  docker-compose.yml
  .env
  data/            created on first start
```

`docker-compose.yml`:

```yaml
services:
  yt2pod:
    image: yt2pod:${YT2POD_VERSION:?set YT2POD_VERSION in .env}
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:?set PUBLIC_BASE_URL in .env}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-}
      CHECK_INTERVAL_MINUTES: ${CHECK_INTERVAL_MINUTES:-60}
```

`.env` (Compose reads it automatically from the folder you run it in):

```bash
YT2POD_VERSION=v0.1.0
PUBLIC_BASE_URL=https://pod.example.com
ADMIN_PASSWORD=change-me
```

The `:?` syntax makes Compose refuse to start if a required value is missing, instead of quietly
running the wrong image or serving feeds that point to the wrong address. The file contains the
admin password, so restrict it with `chmod 600 .env`.

### Updating and rolling back

```bash
# in the repository
make release VERSION=v0.2.0
make build

# in /srv/yt2pod
#   set YT2POD_VERSION=v0.2.0 in .env, then:
docker compose up -d
```

To roll back, set the previous version in `.env` and run `docker compose up -d` again. Data in
`./data` is kept across updates and rollbacks.

### Production on another machine

The image has to be on the machine that runs it. Either copy it directly:

```bash
docker save yt2pod:v0.2.0 | ssh server docker load
```

or push it to a registry (e.g. `ghcr.io/<you>/yt2pod:v0.2.0`) and use that full name in the
production compose file's `image:` line.

### HTTPS and reverse proxy

The app serves plain HTTP. Put a reverse proxy such as Caddy, Traefik or nginx in front of it
and set `PUBLIC_BASE_URL` to the public HTTPS address. HTTPS matters for two reasons: many podcast
apps require it, and with `ADMIN_PASSWORD` set the password would otherwise be sent unencrypted.

Example with Caddy:

```
pod.example.com {
    reverse_proxy localhost:8000
}
```

### Moving existing data

To keep subscriptions and downloads from development, stop the app and copy the `data/` folder
into the production folder before the first start.

---

## Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| Feed works in the browser but not in the podcast app | `PUBLIC_BASE_URL` is not set to an address the phone can reach. The repository's `docker-compose.yml` defaults to `http://localhost:8000`. |
| "Sign in to confirm you're not a bot" errors | YouTube rate limiting, often after many requests in a short time. Wait, increase `CHECK_INTERVAL_MINUTES`, or provide `DATA_DIR/cookies.txt`. |
| Downloads suddenly fail for every video | yt-dlp is probably outdated. Rebuild the image to get the newest version. |
| "No supported JavaScript runtime" warning | Running locally without `deno` or `node` on the `PATH`. |
| `make rollback` fails with "pull access denied" | No image with that version exists locally. Pass `VERSION=` with a version you built before (`docker images yt2pod`). |
| `make release` says "Pass the new version explicitly" | Run it as `make release VERSION=vX.Y.Z`. |

---

## Known limitations

- **Only simple schema upgrades.** New columns are added automatically (`ADDED_COLUMNS`), but
  renaming or changing columns needs a manual migration.
- **Existing files are never renamed or re-downloaded** when you change a subscription's settings.
- **Episodes can't be deleted individually** in the UI. Deleting a subscription can remove all of
  its files.
- **One instance only.** The worker runs inside the web server process, so don't run several
  containers against the same data folder.
