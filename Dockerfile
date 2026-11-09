FROM denoland/deno:bin-2.5.6 AS deno

FROM python:3.12-slim

# ffmpeg: audio extraction / merging; deno: JS runtime yt-dlp needs for YouTube
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY yt2pod ./yt2pod

# Set by `docker compose build` from YT2POD_VERSION (e.g. `git describe --tags`)
ARG VERSION=dev
LABEL org.opencontainers.image.version=$VERSION
ENV YT2POD_VERSION=$VERSION

ENV DATA_DIR=/data \
    PORT=8000 \
    CHECK_INTERVAL_MINUTES=60 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "yt2pod"]
