"""PostgreSQL queue wakeups without periodic idle SQL queries."""

from __future__ import annotations

import logging
import select
import threading
from time import monotonic

import psycopg
from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)
QUEUE_CHANNEL = "datamover_queue_changed"


class QueueListener:
    """Own one autocommit connection used only for LISTEN and socket reads."""

    def __init__(
        self, database_url: str, stop_event: threading.Event, *, retry_seconds: float = 60.0
    ) -> None:
        self.database_url = database_url
        self.stop_event = stop_event
        self.retry_seconds = retry_seconds
        self._retry_at = 0.0
        self.connection: psycopg.Connection | None = None

    @property
    def connected(self) -> bool:
        return self.connection is not None

    def connect(self) -> bool:
        if self.connection is not None:
            return True
        if self.stop_event.is_set() or monotonic() < self._retry_at:
            return False
        try:
            url = make_url(self.database_url)
            args, kwargs = url.get_dialect()().create_connect_args(url)
            kwargs.setdefault("connect_timeout", 5)
            kwargs.setdefault("keepalives", 1)
            kwargs.setdefault("keepalives_idle", 60)
            kwargs.setdefault("keepalives_interval", 10)
            kwargs.setdefault("keepalives_count", 3)
            self.connection = psycopg.connect(*args, **kwargs, autocommit=True)
            self.connection.execute(f"LISTEN {QUEUE_CHANNEL}")
            log.info("Queue notifications connected; idle queue polling suspended")
            return True
        except Exception as exc:
            self._unavailable(exc)
            return False

    def wait(self, timeout: float) -> bool:
        """Wait for a committed change or deadline; stop checks issue no SQL."""

        if self.connection is None:
            self.stop_event.wait(timeout)
            return False
        deadline = monotonic() + max(0.0, timeout)
        while not self.stop_event.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            try:
                readable, _, _ = select.select([self.connection], [], [], min(remaining, 1.0))
                if not readable:
                    continue
                # This connection has a single owner and executes no commands
                # after LISTEN. Consume pending libpq messages without a query.
                self.connection.pgconn.consume_input()
                changed = False
                while self.connection.pgconn.notifies() is not None:
                    changed = True
                if changed:
                    return True
            except Exception as exc:
                self._unavailable(exc)
                return True  # Reconnect and inspect durable state before waiting again.
        return False

    def _unavailable(self, error: Exception) -> None:
        self.close()
        # Keep the retry deadline after a successful LISTEN followed by a drop.
        # Otherwise reconnect + recovery can spin without reaching fallback wait.
        self._retry_at = monotonic() + self.retry_seconds
        # Connection errors can include passwords/DSNs. Record only the type.
        log.warning(
            "Queue notifications unavailable; using recovery polling until reconnected (%s)",
            type(error).__name__,
        )

    def close(self) -> None:
        connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()
