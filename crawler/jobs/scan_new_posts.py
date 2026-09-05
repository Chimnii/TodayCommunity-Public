from __future__ import annotations

import argparse
import http.client
import json
import socket
import sys
import time
from dataclasses import asdict
from typing import Callable, Dict, Iterable, List, Optional
from urllib import error, request

from crawler.archive_stats import (
    PostStatsTransition,
    load_subject_counts,
    normalized_subject,
    stats_deltas,
    stats_mutation_statements,
)
from crawler.config import get_env, get_required_env, is_truthy
from crawler.d1 import (
    D1Client,
    D1Statement,
    d1_usage_label,
    d1_usage_summary,
)
from crawler.parsers.dcinside import (
    DcinsideListParser,
    is_qualifying_post,
    meets_collection_threshold,
)
from crawler.state import source_state_initialization_statement
from crawler.targets import TargetBoard, canonical_post_key, get_target
from crawler.timestamps import (
    TIME_BASES,
    TIME_PRECISIONS,
    canonicalize_utc_text,
    utc_now,
)


DEFAULT_USER_AGENT = (
    "TodayCommunity/1.0 "
    "(metadata archive; +https://github.com/Chimnii/TodayCommunity-Public)"
)
DEFAULT_PAGE_DELAY_SECONDS = 15.0
BLOCKED_HTML_HINTS = (
    ("cf-browser-verification", "Cloudflare browser challenge"),
    ("/cdn-cgi/challenge-platform/", "Cloudflare browser challenge"),
    ("cf-chl-", "Cloudflare browser challenge"),
    ("<title>attention required! | cloudflare</title>", "Cloudflare access challenge"),
    ("<title>access denied</title>", "access denied page"),
    ('class="g-recaptcha"', "captcha challenge"),
    ('id="captcha"', "captcha challenge"),
)
POST_UPSERT_BOUND_PARAMETERS = 16
POST_UPSERT_SHARED_PARAMETERS = 1
MAX_D1_BOUND_PARAMETERS = 100
POSTS_PER_UPSERT = (
    MAX_D1_BOUND_PARAMETERS - POST_UPSERT_SHARED_PARAMETERS
) // POST_UPSERT_BOUND_PARAMETERS
EXISTING_POST_IDS_PER_QUERY = MAX_D1_BOUND_PARAMETERS - 1


class CrawlError(RuntimeError):
    """Base error for crawl failures that should stop the run."""


class CrawlBlockedError(CrawlError):
    """Raised when the source appears to be rate limited or blocked."""


class CrawlSourceError(CrawlError):
    """Raised when the source response is unavailable or unparsable."""


class CrawlTransientError(CrawlSourceError):
    """Raised for a temporary transport failure that is safe to retry."""


class CrawlTimeoutError(CrawlTransientError):
    """Raised when a source request fails because its transport timed out."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan target boards for qualifying posts.")
    parser.add_argument("--target", default="dcinside-singularity")
    parser.add_argument("--pages", type=int, default=int(get_env("TC_SCAN_PAGES", "1") or "1"))
    parser.add_argument("--persist", action="store_true", default=is_truthy(get_env("TC_PERSIST", "0")))
    return parser.parse_args()


def fetch_html(
    url: str,
    timeout_seconds: float = 30.0,
    *,
    open_url: Optional[Callable[..., object]] = None,
) -> str:
    http_request = request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    transport = open_url or request.urlopen
    try:
        with transport(http_request, timeout=max(1.0, timeout_seconds)) as response:
            html = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        if exc.code in {403, 429, 430}:
            raise CrawlBlockedError(f"Blocked by source with HTTP {exc.code} while requesting {url}.") from exc
        if exc.code == 408 or 500 <= exc.code <= 599:
            raise CrawlTransientError(
                f"Source returned transient HTTP {exc.code} while requesting {url}."
            ) from exc
        raise CrawlSourceError(f"Source returned HTTP {exc.code} while requesting {url}.") from exc
    except error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise CrawlTimeoutError(f"Timed out while requesting {url}.") from exc
        raise CrawlTransientError(
            f"Failed to reach source while requesting {url}: {exc.reason}."
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise CrawlTimeoutError(f"Timed out while requesting {url}.") from exc
    except (http.client.HTTPException, ConnectionError) as exc:
        raise CrawlTransientError(
            f"Connection was interrupted while requesting {url}: {exc}."
        ) from exc

    blocked_reason = detect_blocked_html(html)
    if blocked_reason:
        raise CrawlBlockedError(
            f"Blocked by source: detected {blocked_reason} while requesting {url}."
        )

    return html


def scan_target(target: TargetBoard, pages: int, page_delay_seconds: float) -> Dict[str, object]:
    deduped: Dict[str, dict] = {}
    scanned_posts = 0
    scanned_pages = 0

    for page in range(1, max(1, pages) + 1):
        if page > 1 and page_delay_seconds > 0:
            time.sleep(page_delay_seconds)

        html = fetch_html(target.list_url_template.format(page=page))
        parser = DcinsideListParser(
            base_url=target.board_url,
            min_upvotes=target.min_upvotes,
            min_comments=target.min_comments,
            policy=target.policy,
            subject_cell_mode=target.subject_cell_mode,
        )
        parser.feed(html)
        if not parser.diagnostics.is_collection_safe:
            raise CrawlSourceError(
                f"Page {page} had no safely isolatable collection result."
            )

        scanned_posts += len(parser.posts)
        scanned_pages = page

        for post in parser.posts:
            if not is_qualifying_post(
                post,
                target.min_upvotes,
                target.min_comments,
                policy=target.policy,
            ):
                continue
            deduped[post.external_post_id] = asdict(post)

    return {
        "posts": sorted(deduped.values(), key=lambda item: item["created_at"], reverse=True),
        "scanned_pages": scanned_pages,
        "scanned_posts": scanned_posts,
    }


def _execute_statements(
    client: D1Client,
    statements: Iterable[D1Statement],
    *,
    label: Optional[str] = None,
    labels: Optional[Iterable[str]] = None,
) -> None:
    prepared = list(statements)
    if not prepared:
        return
    prepared_labels = tuple(labels) if labels is not None else None
    if prepared_labels is not None and len(prepared_labels) != len(prepared):
        raise ValueError("D1 statement labels must match the statement count")

    # D1 executes the ordered statements in one REST request. Query-only
    # SQLite/local clients keep the same SQL path for tests and local tools.
    batch = getattr(client, "batch", None)
    if callable(batch):
        labeled_batch = getattr(client, "batch_with_labels", None)
        if prepared_labels is not None and callable(labeled_batch):
            labeled_batch(prepared, prepared_labels)
        elif label is not None:
            with d1_usage_label(client, label):
                batch(prepared)
        else:
            batch(prepared)
        return

    for index, (sql, params) in enumerate(prepared):
        statement_label = (
            prepared_labels[index]
            if prepared_labels is not None
            else label
        )
        if statement_label is not None:
            with d1_usage_label(client, statement_label):
                client.query(sql, params)
        else:
            client.query(sql, params)


def upsert_source(client: D1Client, target: TargetBoard, run_started_at: str) -> None:
    run_started_at = canonicalize_utc_text(run_started_at)
    archive = target.archive
    source_state_statement = source_state_initialization_statement(
        target.key,
        run_started_at,
    )
    statements = [
        (
            """
            INSERT INTO archives (
              archive_key, display_name, description, display_order, is_public, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(archive_key) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              display_order = excluded.display_order,
              is_public = excluded.is_public,
              updated_at = excluded.updated_at
            WHERE archives.display_name IS NOT excluded.display_name
               OR archives.description IS NOT excluded.description
               OR archives.display_order IS NOT excluded.display_order
               OR archives.is_public IS NOT excluded.is_public
            """,
            [
                archive.key,
                archive.display_name,
                archive.description,
                archive.display_order,
                int(archive.is_public),
                run_started_at,
                run_started_at,
            ],
        ),
        (
            """
            INSERT INTO sources (
              source_key, archive_key, site_name, board_name, board_url,
              min_upvotes, min_comments, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
              archive_key = excluded.archive_key,
              site_name = excluded.site_name,
              board_name = excluded.board_name,
              board_url = excluded.board_url,
              min_upvotes = excluded.min_upvotes,
              min_comments = excluded.min_comments,
              updated_at = excluded.updated_at
            WHERE sources.archive_key IS NOT excluded.archive_key
               OR sources.site_name IS NOT excluded.site_name
               OR sources.board_name IS NOT excluded.board_name
               OR sources.board_url IS NOT excluded.board_url
               OR sources.min_upvotes IS NOT excluded.min_upvotes
               OR sources.min_comments IS NOT excluded.min_comments
            """,
            [
                target.key,
                archive.key,
                target.site_name,
                target.board_name,
                target.board_url,
                target.min_upvotes,
                target.min_comments,
                run_started_at,
                run_started_at,
            ],
        ),
        (
            """
            INSERT OR IGNORE INTO archive_stats (
              archive_key, active_post_count, latest_seen_at,
              subject_options_json, stats_version, updated_at
            ) VALUES (?, 0, '', '[]', 0, ?)
            """,
            [archive.key, run_started_at],
        ),
        source_state_statement,
    ]

    _execute_statements(client, statements, label="source.bootstrap")


def record_run(
    client: D1Client,
    target: TargetBoard,
    status: str,
    scanned_pages: int,
    scanned_posts: int,
    matched_posts: int,
    run_started_at: str,
    error_message: str = "",
    ensure_source: bool = True,
    run_type: str = "new_posts",
) -> None:
    run_started_at = canonicalize_utc_text(run_started_at)
    if ensure_source:
        upsert_source(client, target, run_started_at)
    with d1_usage_label(client, "run.log"):
        client.query(
            """
            INSERT INTO crawl_runs (
              source_key, run_type, status, scanned_pages, scanned_posts, matched_posts, started_at, finished_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                target.key,
                run_type,
                status,
                scanned_pages,
                scanned_posts,
                matched_posts,
                run_started_at,
                utc_now(),
                error_message[:500],
            ],
        )


def persist_posts(
    client: D1Client,
    target: TargetBoard,
    posts: List[dict],
    scanned_pages: int,
    scanned_posts: int,
    run_started_at: str,
) -> None:
    upsert_source(client, target, run_started_at)

    upsert_posts(client, target, posts, run_started_at)

    record_run(
        client,
        target=target,
        status="completed",
        scanned_pages=scanned_pages,
        scanned_posts=scanned_posts,
        matched_posts=len(posts),
        run_started_at=run_started_at,
        ensure_source=False,
    )


def upsert_posts(
    client: D1Client,
    target: TargetBoard,
    posts: List[dict],
    checked_at: str,
    on_batch_persisted: Optional[Callable[[int], None]] = None,
) -> None:
    checked_at = canonicalize_utc_text(checked_at)
    for offset in range(0, len(posts), POSTS_PER_UPSERT):
        observed_chunk = posts[offset : offset + POSTS_PER_UPSERT]
        distinct = {}
        for post in observed_chunk:
            key = str(post["external_post_id"]).strip()
            distinct[key] = {**post, "subject": distinct.get(key, post)["subject"]}
        chunk = list(distinct.values())
        value_clause = ",\n              ".join(
            """(
                ?, (SELECT archive_key FROM target_archive), ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
              )"""
            for _ in chunk
        )
        params = [target.archive_key]
        canonical_keys = []
        for post in chunk:
            external_post_id = str(post["external_post_id"]).strip()
            canonical_key = canonical_post_key(target, external_post_id)
            created_at = canonicalize_utc_text(post["created_at"])
            created_at_basis = str(post["created_at_basis"])
            created_at_precision = str(post["created_at_precision"])
            if created_at_basis not in TIME_BASES:
                raise ValueError(f"unsupported created_at_basis: {created_at_basis!r}")
            if created_at_precision not in TIME_PRECISIONS:
                raise ValueError(
                    f"unsupported created_at_precision: {created_at_precision!r}"
                )
            canonical_keys.append(canonical_key)
            params.extend(
                [
                    target.key,
                    canonical_key,
                    external_post_id,
                    post["post_url"],
                    post["subject"],
                    post["title"],
                    created_at,
                    post["created_at_raw"],
                    created_at_basis,
                    created_at_precision,
                    post["upvotes"],
                    post["comments"],
                    checked_at,
                    checked_at,
                    checked_at,
                    post["qualifies_by"],
                ]
            )
        # Subject is intentionally insert-only. Rows that existed before the
        # additive migration keep the empty default instead of being backfilled
        # by routine metric refreshes.
        upsert_sql = f"""
            WITH target_archive(archive_key) AS (VALUES (?)),
            incoming(source_key, archive_key, canonical_post_key, external_post_id,
              post_url, subject, title, created_at, created_at_raw,
              created_at_basis, created_at_precision, upvotes, comments,
              fetched_at, first_seen_at, last_seen_at, qualifies_by) AS (
              VALUES {value_clause}
            )
            INSERT INTO posts (
              source_key, archive_key, canonical_post_key, external_post_id,
              post_url, subject, title, created_at, created_at_raw,
              created_at_basis, created_at_precision, upvotes, comments,
              fetched_at, first_seen_at, last_seen_at, qualifies_by
            ) SELECT * FROM incoming
            WHERE NOT EXISTS (
              SELECT 1 FROM posts AS current
              WHERE ((current.archive_key = incoming.archive_key
                      AND current.canonical_post_key = incoming.canonical_post_key)
                 OR (current.source_key = incoming.source_key
                      AND current.external_post_id = incoming.external_post_id))
                AND current.canonical_post_key IS NOT NULL
                AND TRIM(current.canonical_post_key) <> ''
                AND current.created_at IS incoming.created_at
                AND current.status = 'active'
            )
            ON CONFLICT DO UPDATE SET
              archive_key = CASE
                WHEN posts.canonical_post_key IS NULL
                  OR TRIM(posts.canonical_post_key) = ''
                THEN excluded.archive_key
                ELSE posts.archive_key
              END,
              canonical_post_key = CASE
                WHEN posts.canonical_post_key IS NULL
                  OR TRIM(posts.canonical_post_key) = ''
                THEN excluded.canonical_post_key
                ELSE posts.canonical_post_key
              END,
              post_url = excluded.post_url,
              title = excluded.title,
              created_at = excluded.created_at,
              created_at_raw = excluded.created_at_raw,
              created_at_basis = excluded.created_at_basis,
              created_at_precision = excluded.created_at_precision,
              upvotes = excluded.upvotes,
              comments = excluded.comments,
              fetched_at = excluded.fetched_at,
              last_seen_at = excluded.last_seen_at,
              qualifies_by = excluded.qualifies_by,
              status = 'active'
            WHERE posts.canonical_post_key IS NULL
               OR TRIM(posts.canonical_post_key) = ''
               OR posts.created_at IS NOT excluded.created_at
               OR posts.status IS NOT 'active'
            """
        heartbeat_placeholders = ", ".join("?" for _ in canonical_keys)
        heartbeat_sql = f"""
            UPDATE posts
            SET fetched_at = ?,
                last_seen_at = ?
            WHERE archive_key = ?
              AND canonical_post_key IN ({heartbeat_placeholders})
              AND (
                fetched_at IS NOT ?
                OR last_seen_at IS NOT ?
              )
            """
        heartbeat_params = [
            checked_at,
            checked_at,
            target.archive_key,
            *canonical_keys,
            checked_at,
            checked_at,
        ]

        # Derive count transitions from the current database state inside the
        # atomic batch, before mutating posts. A stale Python pre-read can double
        # count an overlapping insert or miss a concurrent visibility change.
        stats_statements = _observation_stats_statements(target, chunk, canonical_keys, checked_at)

        # Structural changes and restoration keep the full conflict path.
        # Metric-only changes must not name identity, time-sort, or status
        # columns in SET: D1 charges for the associated index maintenance.
        metric_statements = _metric_update_statements(
            target.archive_key, chunk, canonical_keys, checked_at
        )
        _execute_statements(
            client,
            [
                *stats_statements,
                (upsert_sql, params),
                *metric_statements,
                _content_update_statement(target.archive_key, chunk, canonical_keys, checked_at),
                (heartbeat_sql, heartbeat_params),
            ],
            labels=(
                *("stats" for _ in stats_statements),
                "post.upsert",
                *("post.metrics" for _ in metric_statements),
                "post.content",
                "post.heartbeat",
            ),
        )
        if on_batch_persisted is not None:
            on_batch_persisted(len(observed_chunk))


def _observation_stats_statements(target, posts, canonical_keys, checked_at):
    values = ", ".join("(?, ?, ?)" for _ in posts)
    params = [value for key, post in zip(canonical_keys, posts)
              for value in (key, str(post["external_post_id"]).strip(), normalized_subject(post["subject"]))]
    changes = f"""
        WITH incoming(key, external_id, subject) AS (VALUES {values}),
        additions AS (
          SELECT trim(CASE WHEN current.id IS NOT NULL THEN coalesce(current.subject, '')
                           WHEN legacy.id IS NOT NULL THEN coalesce(legacy.subject, '')
                           ELSE incoming.subject END) AS subject
          FROM incoming
          LEFT JOIN posts AS current
            ON current.archive_key = ? AND current.canonical_post_key = incoming.key
          LEFT JOIN posts AS legacy
            ON current.id IS NULL AND legacy.source_key = ?
              AND legacy.external_post_id = incoming.external_id
          WHERE coalesce(current.status, legacy.status, '') <> 'active'
        )
    """
    params.extend((target.archive_key, target.key))
    return [
        ("""INSERT OR IGNORE INTO archive_stats (
            archive_key, active_post_count, latest_seen_at,
            subject_options_json, stats_version, updated_at
          ) VALUES (?, 0, '', '[]', 0, ?)""", [target.archive_key, checked_at]),
        (changes + """
          UPDATE archive_stats
          SET active_post_count = active_post_count + (SELECT count(*) FROM additions),
              latest_seen_at = max(latest_seen_at, ?),
              stats_version = stats_version + 1, updated_at = ?
          WHERE archive_key = ?
        """, [*params, checked_at, checked_at, target.archive_key]),
        (changes + """
          INSERT INTO archive_subject_stats (archive_key, subject, active_post_count, updated_at)
          SELECT ?, subject, count(*), ? FROM additions
          WHERE subject <> '' GROUP BY subject
          ON CONFLICT(archive_key, subject) DO UPDATE SET
            active_post_count = archive_subject_stats.active_post_count + excluded.active_post_count,
            updated_at = excluded.updated_at
        """, [*params, target.archive_key, checked_at]),
        ("""
          UPDATE archive_stats
          SET subject_options_json = coalesce((
            SELECT json_group_array(subject) FROM (
              SELECT subject FROM archive_subject_stats
              WHERE archive_key = ? AND active_post_count > 0 AND length(subject) <= 100
              ORDER BY subject COLLATE NOCASE ASC, subject ASC LIMIT 100
            )
          ), '[]')
          WHERE archive_key = ? AND changes() > 0
        """, [target.archive_key, target.archive_key]),
    ]


def _metric_update_statements(archive_key, posts, canonical_keys, checked_at):
    latest_posts = dict(zip(canonical_keys, posts))
    values = ", ".join("(?, ?, ?)" for _ in latest_posts)
    incoming_params = [
        value
        for key, post in latest_posts.items()
        for value in (key, post["upvotes"], post["comments"])
    ]
    statements = []
    # Both first; after it runs, the two single-column paths cannot rewrite
    # those rows. All predicates are evaluated inside the same atomic batch.
    for columns, predicate in (
        (("upvotes", "comments"), "posts.upvotes IS NOT incoming.upvotes AND posts.comments IS NOT incoming.comments"),
        (("upvotes",), "posts.upvotes IS NOT incoming.upvotes AND posts.comments IS incoming.comments"),
        (("comments",), "posts.comments IS NOT incoming.comments AND posts.upvotes IS incoming.upvotes"),
    ):
        assignments = ", ".join(
            f"{column} = incoming.{column}"
            for column in columns
        )
        statements.append((
            f"""
            WITH incoming(key, upvotes, comments) AS (VALUES {values})
            UPDATE posts
            SET {assignments}, fetched_at = ?, last_seen_at = ?
            FROM incoming
            WHERE posts.archive_key = ?
              AND posts.canonical_post_key = incoming.key
              AND ({predicate})
            """,
            [*incoming_params, checked_at, checked_at, archive_key],
        ))
    return statements


def _content_update_statement(archive_key, posts, canonical_keys, checked_at):
    # Preserve the latest source evidence without rewriting time/metric indexes.
    # Subject and first-source ownership remain insert-only.
    columns = ("post_url", "title", "created_at_raw", "created_at_basis",
               "created_at_precision", "qualifies_by")
    latest_posts = dict(zip(canonical_keys, posts))
    values = ", ".join("(" + ", ".join("?" for _ in range(len(columns) + 1)) + ")"
                       for _ in latest_posts)
    params = [value for key, post in latest_posts.items()
              for value in (key, *(post[column] for column in columns))]
    assignments = ", ".join(f"{column} = incoming.{column}" for column in columns)
    changed = " OR ".join(f"posts.{column} IS NOT incoming.{column}" for column in columns)
    return (f"""
        WITH incoming(key, {', '.join(columns)}) AS (VALUES {values})
        UPDATE posts SET {assignments}, fetched_at = ?, last_seen_at = ?
        FROM incoming
        WHERE posts.archive_key = ? AND posts.canonical_post_key = incoming.key
          AND ({changed})
        """, [*params, checked_at, checked_at, archive_key])


def post_upsert_query_count(post_count: int) -> int:
    normalized = max(0, int(post_count))
    if normalized == 0:
        return 0
    return (normalized + POSTS_PER_UPSERT - 1) // POSTS_PER_UPSERT


def existing_post_lookup_query_count(post_count: int) -> int:
    normalized = max(0, int(post_count))
    if normalized == 0:
        return 0
    return (
        normalized + EXISTING_POST_IDS_PER_QUERY - 1
    ) // EXISTING_POST_IDS_PER_QUERY


def detect_blocked_html(html: str) -> str:
    """Return a high-confidence block reason without trusting post text."""

    lowered = html.casefold()
    # A complete board response can legitimately contain words such as
    # "captcha" or "access denied" in a post title. Challenge-text hints are
    # therefore considered only when the normal board-row marker is absent.
    if "ub-content" in lowered:
        return ""
    for pattern, description in BLOCKED_HTML_HINTS:
        if pattern in lowered:
            return description
    return ""


def update_finalized_posts(
    client: D1Client,
    target: TargetBoard,
    posts: List[dict],
    checked_at: str,
) -> None:
    """Persist final metrics without inserting posts that never qualified."""

    if not posts:
        return

    existing_canonical_keys = set()
    for offset in range(0, len(posts), EXISTING_POST_IDS_PER_QUERY):
        chunk = posts[offset : offset + EXISTING_POST_IDS_PER_QUERY]
        canonical_keys = [
            canonical_post_key(target, post["external_post_id"])
            for post in chunk
        ]
        placeholders = ", ".join("?" for _ in chunk)
        with d1_usage_label(client, "post.lookup"):
            existing_rows = client.query(
                f"""
                SELECT canonical_post_key
                FROM posts
                WHERE archive_key = ?
                  AND canonical_post_key IN ({placeholders})
                """,
                [target.archive_key, *canonical_keys],
            )
        existing_canonical_keys.update(
            str(row["canonical_post_key"])
            for row in existing_rows
        )
    persistable = [
        post
        for post in posts
        if (
            target.collect_all
            or meets_collection_threshold(
                post["upvotes"],
                post["comments"],
                min_upvotes=target.min_upvotes,
                min_comments=target.min_comments,
                policy=target.policy,
            )
            or canonical_post_key(target, post["external_post_id"])
            in existing_canonical_keys
        )
    ]
    upsert_posts(client, target, persistable, checked_at)


def mark_posts_deleted(
    client: D1Client,
    target: TargetBoard,
    external_post_ids: Iterable[object],
) -> int:
    """Soft-delete previously archived rows backed by confirmed absence evidence."""

    normalized_ids = list(
        dict.fromkeys(
            str(external_post_id).strip()
            for external_post_id in external_post_ids
            if str(external_post_id).strip()
        )
    )
    deleted_count = 0
    for offset in range(0, len(normalized_ids), EXISTING_POST_IDS_PER_QUERY):
        chunk = normalized_ids[offset : offset + EXISTING_POST_IDS_PER_QUERY]
        placeholders = ", ".join("?" for _ in chunk)
        with d1_usage_label(client, "post.lookup"):
            active_rows = client.query(
                f"""
                SELECT external_post_id, subject, status
                FROM posts
                WHERE source_key = ?
                  AND external_post_id IN ({placeholders})
                  AND status = 'active'
                """,
                [target.key, *chunk],
            )
        active_ids = [str(row["external_post_id"]) for row in active_rows]
        if not active_ids:
            continue

        active_placeholders = ", ".join("?" for _ in active_ids)
        transitions = [
            PostStatsTransition(
                old_status="active",
                old_subject=normalized_subject(row.get("subject")),
                new_status="deleted",
                new_subject=normalized_subject(row.get("subject")),
            )
            for row in active_rows
        ]
        _, subject_deltas = stats_deltas(transitions)
        subject_counts = load_subject_counts(
            client,
            target.archive_key,
            subject_deltas,
        )
        changed_at = utc_now()
        stats_statements = stats_mutation_statements(
            target.archive_key,
            transitions,
            changed_at,
            previous_subject_counts=subject_counts,
        )
        _execute_statements(
            client,
            [
                (
                    f"""
                    UPDATE posts
                    SET status = 'deleted'
                    WHERE source_key = ?
                      AND external_post_id IN ({active_placeholders})
                      AND status = 'active'
                    """,
                    [target.key, *active_ids],
                ),
                *stats_statements,
            ],
            labels=(
                "post.update",
                *("stats" for _ in stats_statements),
            ),
        )
        deleted_count += len(active_ids)
    return deleted_count


def get_page_delay_seconds() -> float:
    raw_value = get_env("TC_SCAN_PAGE_DELAY_SECONDS", str(DEFAULT_PAGE_DELAY_SECONDS))
    try:
        delay = float(raw_value)
    except ValueError:
        return DEFAULT_PAGE_DELAY_SECONDS
    return max(0.0, delay)


def main() -> None:
    args = parse_args()
    target = get_target(args.target)
    run_started_at = utc_now()
    page_delay_seconds = get_page_delay_seconds()
    client = None
    if args.persist:
        client = D1Client(
            account_id=get_required_env("TC_CF_ACCOUNT_ID"),
            database_id=get_required_env("TC_CF_DATABASE_ID"),
            api_token=get_required_env("TC_CF_API_TOKEN"),
        )

    try:
        scan_result = scan_target(target=target, pages=args.pages, page_delay_seconds=page_delay_seconds)
        posts = scan_result["posts"]
        scanned_pages = int(scan_result["scanned_pages"])
        scanned_posts = int(scan_result["scanned_posts"])

        result = {
            "target": target.key,
            "pages": args.pages,
            "scanned_pages": scanned_pages,
            "scanned_posts": scanned_posts,
            "matched_posts": len(posts),
            "posts": posts,
        }
        if client:
            persist_posts(
                client,
                target=target,
                posts=posts,
                scanned_pages=scanned_pages,
                scanned_posts=scanned_posts,
                run_started_at=run_started_at,
            )
            usage = d1_usage_summary(client)
            if usage is not None:
                result["d1_usage"] = usage
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except CrawlBlockedError as exc:
        if client:
            record_run(
                client,
                target=target,
                status="blocked",
                scanned_pages=0,
                scanned_posts=0,
                matched_posts=0,
                run_started_at=run_started_at,
                error_message=str(exc),
            )

        failure = {
            "target": target.key,
            "status": "blocked",
            "error": str(exc),
        }
        if client:
            usage = d1_usage_summary(client)
            if usage is not None:
                failure["d1_usage"] = usage
        print(
            json.dumps(failure, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except CrawlError as exc:
        if client:
            record_run(
                client,
                target=target,
                status="failed",
                scanned_pages=0,
                scanned_posts=0,
                matched_posts=0,
                run_started_at=run_started_at,
                error_message=str(exc),
            )

        failure = {
            "target": target.key,
            "status": "failed",
            "error": str(exc),
        }
        if client:
            usage = d1_usage_summary(client)
            if usage is not None:
                failure["d1_usage"] = usage
        print(
            json.dumps(failure, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
