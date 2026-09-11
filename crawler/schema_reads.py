"""Bounded retry for explicitly read-only schema inspections."""
from __future__ import annotations

import errno
import time
from urllib.error import HTTPError, URLError


def retry_schema_read(inspect):
    # Never wrap bootstrap/migration or a writer: a lost response may have committed.
    for attempt in range(3):
        try:
            return inspect()
        except (URLError, ConnectionError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            transient = isinstance(reason, (ConnectionResetError, ConnectionAbortedError, TimeoutError))
            transient = transient or (
                isinstance(reason, OSError)
                and reason.errno in {errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT}
            )
            if isinstance(exc, HTTPError) or not transient or attempt == 2:
                raise
            time.sleep(attempt + 1)
