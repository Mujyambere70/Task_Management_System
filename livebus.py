"""
livebus.py - (v4) live board updates across several server processes and machines, without Redis.

Version 3 woke the open WebSockets of the same process at once, and other processes only noticed a change when they
read the events table again (up to 2 seconds later). With PostgreSQL, version 4 uses the database itself as the
message broker:

    a change   ->  SELECT pg_notify('taskflow_live', '<workspace>') inside the same transaction
                   (PostgreSQL delivers it only if the transaction commits)
    every process keeps one extra connection that runs LISTEN taskflow_live; a message wakes its WebSockets at once

So Gunicorn can run several workers, and Fly.io several machines, and every open board still updates at once.
With SQLite (one computer, one process) nothing is needed: the in-process signal is enough.
"""

import threading
import time

CHANNEL = "taskflow_live"


def direct_url(url):
    """LISTEN needs a direct connection: a connection pooler in transaction mode (Neon "-pooler" host, PgBouncer)
    does not pass notifications on. The direct host of Neon is the same name without "-pooler"."""
    url = url.replace("postgres://", "postgresql://", 1)
    return url.replace("-pooler.", ".", 1)


def publish(con, ws):
    """Call inside the transaction that wrote the event."""
    if getattr(con, "is_postgres", False):
        con.execute("SELECT pg_notify(?, ?)", (CHANNEL, str(ws or "")))


class Listener:
    """One background thread per process. connect() returns a raw psycopg connection with autocommit on."""

    def __init__(self, connect, wake, retry_seconds=5):
        self.connect, self.wake, self.retry = connect, wake, retry_seconds
        self.received = 0
        self.running = False
        self.thread = None

    def run_once(self, conn, max_messages=None):
        """LISTEN and wake the local WebSockets for every message (until max_messages, for tests)."""
        conn.execute(f"LISTEN {CHANNEL}")
        for _n in conn.notifies():
            self.received += 1
            self.wake()
            if max_messages and self.received >= max_messages:
                return

    def _loop(self):
        while self.running:
            try:
                conn = self.connect()
                try:
                    self.run_once(conn)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            except Exception:                    # the database restarted, network ... try again soon
                time.sleep(self.retry)

    def start(self):
        if self.thread:
            return self
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="taskflow-livebus", daemon=True)
        self.thread.start()
        return self
