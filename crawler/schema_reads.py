"""Bounded retry for explicitly read-only schema inspections."""
from __future__ import annotations

import errno
import time
from urllib.error import HTTPError, URLError


class _SchemaPragmas:
    """A fresh per-inspection cache, never shared with another inspection."""

    def __init__(self, client):
        self.client = client
        self.rows = {}

    def query(self, sql, params=None):
        if params is None and sql in self.rows:
            return self.rows[sql]
        return self.client.query(sql, params)

    def prefetch(self, statements):
        pending = list(dict.fromkeys(sql for sql in statements if sql not in self.rows))
        for start in range(0, len(pending), 50):
            chunk = pending[start:start + 50]
            if callable(getattr(self.client, "batch", None)):
                results = self.client.batch([(sql, []) for sql in chunk])
                if len(results) != len(chunk):
                    raise ValueError("Incomplete schema inspection batch")
                for sql, result in zip(chunk, results):
                    if isinstance(result, dict):
                        if result.get("success") is False:
                            raise ValueError("Failed schema inspection statement")
                        result = result.get("results")
                    if not isinstance(result, list):
                        raise ValueError("Invalid schema inspection result")
                    self.rows[sql] = result
            else:
                for sql in chunk:
                    self.rows[sql] = self.client.query(sql)


def prime_schema_reads(client, table_names, *, index_tables=None,
                       foreign_key_tables=(), extended_indexes=()):
    """Batch read-only PRAGMAs; keep every existing contract validation."""
    snapshot = _SchemaPragmas(client)
    quote = lambda value: '"' + value.replace('"', '""') + '"'
    names = set(table_names)
    indexed = names if index_tables is None else names.intersection(index_tables)
    snapshot.prefetch([
        *(f"PRAGMA table_info({quote(name)})" for name in sorted(names)),
        *(f"PRAGMA index_list({quote(name)})" for name in sorted(indexed)),
        *(f"PRAGMA foreign_key_list({quote(name)})" for name in sorted(names.intersection(foreign_key_tables))),
    ])
    indexes = [row for table in sorted(indexed)
               for row in snapshot.query(f"PRAGMA index_list({quote(table)})")]
    required = set(extended_indexes)
    snapshot.prefetch([
        *(f"PRAGMA index_info({quote(str(row['name']))})" for row in indexes
          if str(row.get("unique")) == "1" and str(row.get("partial", 0)) == "0"),
        *(f"PRAGMA index_xinfo({quote(str(row['name']))})" for row in indexes
          if row["name"] in required),
    ])
    return snapshot


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
