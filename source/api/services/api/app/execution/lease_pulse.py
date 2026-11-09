"""Renew the existing fenced lease independently of synchronous validation.

No expired lease may be renewed, no owner may be substituted, and no model
request is retried. Database failure cancels the task conservatively.
"""
import asyncio
import threading
import time


class LeasePulse:
    def __init__(self, run_id, owner, task):
        from app.execution.storage import LEASE_SECONDS
        self.run_id, self.owner, self.task = run_id, owner, task
        self.loop = asyncio.get_running_loop()
        self.interval = min(5.0, LEASE_SECONDS / 4)
        self.stopped = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self._run, name='execution-lease', daemon=True)

    def start(self):
        self.thread.start()

    def _cancel_if_active(self):
        if not self.stopped.is_set() and not self.task.done():
            self.task.cancel()

    def _run(self):
        from app.execution.storage import heartbeat
        previous = time.monotonic()
        while not self.stopped.wait(self.interval):
            now = time.monotonic()
            try:
                renewed = heartbeat(self.run_id, self.owner)
                cause = 'LEASE_LOST'
            except Exception:
                renewed, cause = False, 'LEASE_HEARTBEAT_UNAVAILABLE'
            if not renewed:
                self.failure = {'cause': cause, 'phase': 'lease_heartbeat',
                    'heartbeat_interval_ms': round((now - previous) * 1000)}
                if not self.stopped.is_set():
                    self.loop.call_soon_threadsafe(self._cancel_if_active)
                return
            previous = now

    async def close(self):
        # Suppress a queued cancellation before releasing the lease. A pending
        # SQLite busy timeout ends within the connection's existing 30s limit.
        self.stopped.set()
        await asyncio.to_thread(self.thread.join)
