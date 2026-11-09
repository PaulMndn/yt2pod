"""Background thread that checks subscriptions periodically and on demand."""

import logging
import queue
import threading
import time

from . import db
from .downloader import run_subscription

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, downloads_dir, archives_dir, interval_minutes):
        self.downloads_dir = downloads_dir
        self.archives_dir = archives_dir
        self.interval = max(1, interval_minutes) * 60
        self._queue = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self.current = None

    def start(self):
        threading.Thread(target=self._consume, name="yt2pod-worker", daemon=True).start()
        threading.Thread(target=self._schedule, name="yt2pod-scheduler", daemon=True).start()

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

    def _schedule(self):
        time.sleep(10)  # let the web server come up first
        while True:
            log.info("Scheduled check of all enabled subscriptions")
            try:
                self.enqueue_all()
            except Exception:  # noqa: BLE001
                log.exception("Scheduling failed")
            time.sleep(self.interval)

    def _consume(self):
        while True:
            sub_id = self._queue.get()
            self.current = sub_id
            try:
                run_subscription(sub_id, self.downloads_dir, self.archives_dir)
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error for subscription %s", sub_id)
            finally:
                self.current = None
                with self._lock:
                    self._pending.discard(sub_id)
