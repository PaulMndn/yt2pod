"""Background thread that checks subscriptions periodically and on demand."""

import logging
import queue
import threading
import time

from . import db
from .downloader import run_subscription

log = logging.getLogger(__name__)

STARTUP_DELAY = 10  # seconds before the first scheduled check, so the web server is up first


class Worker:
    def __init__(self, downloads_dir, cookies_file, interval_minutes):
        self.downloads_dir = downloads_dir
        self.cookies_file = cookies_file
        self.interval = max(1, interval_minutes) * 60
        self._queue = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self.current = None

    def start(self):
        threading.Thread(target=self._run, name="yt2pod-worker", daemon=True).start()

    def enqueue(self, sub_id):
        with self._lock:
            if sub_id in self._pending:
                return False
            self._pending.add(sub_id)
        self._queue.put(sub_id)
        return True

    def enqueue_all(self):
        for sub_id in db.enabled_subscription_ids():
            self.enqueue(sub_id)

    def is_pending(self, sub_id):
        with self._lock:
            return sub_id in self._pending

    def _run(self):
        """Process queued checks; when the queue is idle until the next scheduled run, queue all."""
        next_run = time.monotonic() + STARTUP_DELAY
        while True:
            wait = next_run - time.monotonic()
            if wait <= 0:
                log.info("Scheduled check of all enabled subscriptions")
                try:
                    self.enqueue_all()
                except Exception:  # noqa: BLE001
                    log.exception("Scheduling failed")
                next_run = time.monotonic() + self.interval
                continue
            try:
                sub_id = self._queue.get(timeout=wait)
            except queue.Empty:
                continue
            self.current = sub_id
            try:
                run_subscription(sub_id, self.downloads_dir, self.cookies_file)
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error for subscription %s", sub_id)
            finally:
                self.current = None
                with self._lock:
                    self._pending.discard(sub_id)
