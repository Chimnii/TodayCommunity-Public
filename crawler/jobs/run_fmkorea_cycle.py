from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from typing import Callable, Dict, List, Optional
from urllib import error, request

from crawler.config import get_env, get_required_env, is_truthy
from crawler.d1 import D1Client
from crawler.jobs.scan_new_posts import (
    CrawlBlockedError,
    CrawlSourceError,
    CrawlTimeoutError,
    CrawlTransientError,
    fetch_html,
    record_run,
    upsert_posts,
    upsert_source,
)
from crawler.parsers.fmkorea import (
    FmkoreaBoardParser,
    FmkoreaPost,
    FmkoreaSearchParser,
    is_fmkorea_qualifying_post,
)
from crawler.runtime import (
    BACKFILL_PHASE,
    HOT_PHASE,
    CycleBlocked,
    CycleRuntime,
    RuntimeLimitReached,
)
from crawler.state import SourceState, get_source_state, save_source_state
from crawler.targets import TargetBoard, get_target


CYCLE_MODE_HOT = "hot"
CYCLE_MODE_BACKFILL = "backfill"
VALID_CYCLE_MODES = (CYCLE_MODE_HOT, CYCLE_MODE_BACKFILL)
DEFAULT_TRANSIENT_FETCH_ATTEMPTS = 2
DEFAULT_FETCH_TIMEOUT_SECONDS = 30.0


@dataclass
class FmkoreaPhaseSummary:
    run_type: str
    status: str = "completed"
    scanned_pages: int = 0
    scanned_posts: int = 0
    matched_posts: int = 0
    persisted_posts: int = 0
    stop_reason: str = ""
    oldest_seen_at: str = ""
    pages: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class FmkoreaPage:
    page: int
    posts: List[FmkoreaPost]
    has_later_page: bool = False
    can_prove_last_page: bool = False

    @property
    def oldest_created_at(self) -> datetime:
        return min(parse_datetime(post.created_at) for post in self.posts)

    @property
    def newest_created_at(self) -> datetime:
        return max(parse_datetime(post.created_at) for post in self.posts)


def fetch_fmkorea_html(
    url: str,
    timeout_seconds: float = 30.0,
    *,
    open_url: Optional[Callable[..., object]] = None,
) -> str:
    """Use the shared transport while treating FMKorea's HTTP 430 as a block."""

    try:
        return fetch_html(url, timeout_seconds, open_url=open_url)
    except error.HTTPError as exc:
        if exc.code == 430:
            raise CrawlBlockedError(
                f"Blocked by FMKorea with HTTP 430 while requesting {url}."
            ) from exc
        raise
    except CrawlSourceError as exc:
        if "HTTP 430" in str(exc):
            raise CrawlBlockedError(
                f"Blocked by FMKorea with HTTP 430 while requesting {url}."
            ) from exc
        raise


class FmkoreaHttpSession:
    """Keep server-issued cookies only for the lifetime of one crawl cycle."""

    def __init__(self) -> None:
        self._cookie_jar = CookieJar()
        self._opener = request.build_opener(
            request.HTTPCookieProcessor(self._cookie_jar)
        )

    def __call__(self, url: str, timeout_seconds: float) -> str:
        return fetch_fmkorea_html(
            url,
            timeout_seconds,
            open_url=self._opener.open,
        )


class FmkoreaCycle:
    """Run one bounded FMKorea Hot or Backfill lane.

    FMKorea page numbers are deliberately retained only as a resume hint. The
    Backfill lane re-reads one page before that hint, and advances it only after
    a collection-safe page has been persisted. It does not create DCInside-style
    numeric coverage claims from a mutable paginated feed.
    """

    def __init__(
        self,
        *,
        target: TargetBoard,
        mode: str,
        client: Optional[D1Client] = None,
        fetcher: Optional[Callable[[str, float], str]] = None,
        now: Optional[datetime] = None,
        runtime: Optional[CycleRuntime] = None,
        transient_fetch_attempts: int = DEFAULT_TRANSIENT_FETCH_ATTEMPTS,
    ) -> None:
        if target.origin_key != "fmkorea":
            raise ValueError(f"Target {target.key!r} is not an FMKorea target.")
        if target.collector_kind not in {"fmkorea-search", "fmkorea-board"}:
            raise ValueError(
                f"Unsupported FMKorea collector kind: {target.collector_kind!r}."
            )
        if mode not in VALID_CYCLE_MODES:
            choices = ", ".join(VALID_CYCLE_MODES)
            raise ValueError(f"Unknown FMKorea mode {mode!r}; expected: {choices}.")
        if not 1 <= transient_fetch_attempts <= 3:
            raise ValueError("transient_fetch_attempts must be between 1 and 3")

        self.target = target
        self.mode = mode
        self.client = client
        self.fetcher = fetcher or FmkoreaHttpSession()
        self.cycle_started_at = ensure_aware(now or datetime.now(timezone.utc)).replace(
            microsecond=0
        )
        self.run_started_at = self.cycle_started_at.astimezone(timezone.utc).isoformat()
        self.transient_fetch_attempts = transient_fetch_attempts
        total_seconds = (
            target.hot_max_seconds
            if mode == CYCLE_MODE_HOT
            else target.backfill_max_seconds
        )
        self.runtime = runtime or CycleRuntime(
            min_request_interval_seconds=target.request_interval_seconds,
            total_seconds=total_seconds,
            hot_seconds=total_seconds,
        )
        self.max_pages = max(
            1,
            int(
                target.hot_max_pages
                if mode == CYCLE_MODE_HOT
                else target.backfill_max_pages
            ),
        )
        self.hot_cutoff = self.cycle_started_at - timedelta(
            minutes=target.hot_lookback_minutes
        )
        self.source_state = SourceState(source_key=target.key)
        self.persistence_warnings: List[str] = []

        if self.client:
            upsert_source(self.client, self.target, self.run_started_at)
            self.source_state = (
                get_source_state(self.client, self.target.key) or self.source_state
            )

    def run(self) -> Dict[str, object]:
        blocked_until = parse_optional_datetime(self.source_state.blocked_until)
        if blocked_until and blocked_until > self.cycle_started_at:
            return self._result(
                status="cooldown",
                summary=None,
                error=(
                    "source cooldown remains active until "
                    f"{blocked_until.astimezone(timezone.utc).isoformat()}"
                ),
            )

        summary = FmkoreaPhaseSummary(run_type=f"fmkorea_{self.mode}")
        try:
            if self.mode == CYCLE_MODE_HOT:
                self._run_hot(summary)
            else:
                self._run_backfill(summary)
        except CycleBlocked as exc:
            summary.status = "blocked"
            summary.stop_reason = exc.reason
            self._record_block(summary, exc.reason)
            return self._result("blocked", summary, str(exc))
        except CrawlBlockedError as exc:
            try:
                self.runtime.block(str(exc))
            except CycleBlocked as blocked:
                summary.status = "blocked"
                summary.stop_reason = blocked.reason
                self._record_block(summary, blocked.reason)
                return self._result("blocked", summary, str(blocked))
        except RuntimeLimitReached as exc:
            summary.status = "partial"
            summary.stop_reason = exc.reason
        except (CrawlTimeoutError, CrawlTransientError, CrawlSourceError) as exc:
            summary.status = "failed"
            summary.stop_reason = str(exc)
            self._record_summary(summary, error_message=str(exc))
            return self._result("failed", summary, str(exc))
        except Exception as exc:
            summary.status = "failed"
            summary.stop_reason = str(exc)
            self._record_summary(summary, error_message=str(exc))
            return self._result("failed", summary, str(exc))

        if self.client:
            try:
                save_source_state(self.client, self.source_state)
            except Exception as exc:
                summary.status = "failed"
                summary.stop_reason = f"Could not save FMKorea resume state: {exc}"
                self._record_summary(summary, error_message=summary.stop_reason)
                return self._result("failed", summary, summary.stop_reason)
        self._record_summary(
            summary,
            error_message=summary.stop_reason if summary.status == "partial" else "",
        )
        return self._result(summary.status, summary)

    def _run_hot(self, summary: FmkoreaPhaseSummary) -> None:
        seen_ids: set[str] = set()
        lookback_reached = False
        for page in range(1, self.max_pages + 1):
            try:
                snapshot = self._fetch_page(page, HOT_PHASE)
            except RuntimeLimitReached as exc:
                summary.status = "partial"
                summary.stop_reason = exc.reason
                break
            self._update_summary(summary, snapshot)
            qualifying = [
                post
                for post in snapshot.posts
                if post.external_post_id not in seen_ids and self._qualifies(post)
            ]
            seen_ids.update(post.external_post_id for post in qualifying)
            summary.matched_posts = len(seen_ids)
            if self.client and qualifying:
                upsert_posts(
                    self.client,
                    self.target,
                    [asdict(post) for post in qualifying],
                    self.run_started_at,
                )
                summary.persisted_posts += len(qualifying)
            if snapshot.newest_created_at <= self.hot_cutoff:
                lookback_reached = True
                summary.stop_reason = "lookback_reached"
                break
            if snapshot.can_prove_last_page:
                lookback_reached = True
                summary.stop_reason = "feed_end_reached"
                break

        if not lookback_reached and not summary.stop_reason:
            summary.status = "partial"
            summary.stop_reason = "page_limit"

    def _run_backfill(self, summary: FmkoreaPhaseSummary) -> None:
        # Reaching the tail completes one bounded pass, not the lifetime of the
        # feed.  A later pass starts from the head so a long Hot outage, mutable
        # pagination, or a post that qualifies late can still be recovered.
        if bool(self.source_state.state_metadata.get("backfill_complete")):
            self.source_state.state_metadata["backfill_complete"] = False
            self.source_state.state_metadata.pop(
                "backfill_last_page_number", None
            )
            self.source_state.state_metadata.pop(
                "backfill_last_page_fingerprint", None
            )
            self.source_state.backfill_page_hint = 1
        saved_hint = positive_int(self.source_state.backfill_page_hint, default=1)
        start_page = max(1, saved_hint - 1)
        seen_ids: set[str] = set()

        for offset in range(self.max_pages):
            page = start_page + offset
            snapshot = self._fetch_page(page, BACKFILL_PHASE)
            self._update_summary(summary, snapshot)
            qualifying = [
                post
                for post in snapshot.posts
                if post.external_post_id not in seen_ids and self._qualifies(post)
            ]
            seen_ids.update(post.external_post_id for post in snapshot.posts)
            summary.matched_posts += len(qualifying)

            fingerprint = ",".join(
                sorted(post.external_post_id for post in snapshot.posts)
            )
            previous_fingerprint = str(
                self.source_state.state_metadata.get(
                    "backfill_last_page_fingerprint", ""
                )
            )
            previous_page = positive_int(
                self.source_state.state_metadata.get("backfill_last_page_number"),
                default=page,
            )
            if (
                previous_fingerprint
                and previous_fingerprint == fingerprint
                and previous_page != page
            ):
                raise CrawlSourceError(
                    "FMKorea returned the same canonical row set for two "
                    f"different pages ({previous_page} and {page}); resume hint "
                    "was not advanced."
                )

            if self.client and qualifying:
                upsert_posts(
                    self.client,
                    self.target,
                    [asdict(post) for post in qualifying],
                    self.run_started_at,
                )
                summary.persisted_posts += len(qualifying)

            # This is a non-authoritative hint. Persist only after the page was
            # parsed safely and all of its qualifying rows were written.
            self.source_state.backfill_page_hint = max(
                saved_hint,
                page + 1,
            )
            self.source_state.state_metadata["backfill_last_page_number"] = page
            self.source_state.state_metadata[
                "backfill_last_page_fingerprint"
            ] = fingerprint
            if snapshot.posts:
                oldest = min(snapshot.posts, key=lambda post: parse_datetime(post.created_at))
                self.source_state.backfill_anchor_post_id = oldest.external_post_id
                self.source_state.backfill_anchor_created_at = oldest.created_at
            if snapshot.can_prove_last_page:
                self.source_state.state_metadata["backfill_complete"] = True
                # Keep the completed-pass marker for observability.  The next
                # Backfill run consumes it and begins a fresh pass at page 1.
                self.source_state.backfill_page_hint = 1
                summary.stop_reason = "backfill_complete"
            if self.client:
                save_source_state(self.client, self.source_state)
            if snapshot.can_prove_last_page:
                return

        summary.status = "partial"
        summary.stop_reason = "page_limit"

    def _fetch_page(self, page: int, phase: str) -> FmkoreaPage:
        html = ""
        last_timeout: Optional[CrawlTimeoutError] = None
        for attempt in range(1, self.transient_fetch_attempts + 1):
            remaining = self.runtime.remaining_seconds(phase)
            spacing = self.runtime.next_request_delay_seconds()
            if remaining <= spacing + 1.0:
                if last_timeout is not None:
                    raise last_timeout
                raise RuntimeLimitReached(
                    scope="cycle" if phase == BACKFILL_PHASE else "phase",
                    reason="request_timeout_budget",
                    phase=phase,
                )
            self.runtime.acquire_request(phase)
            remaining = self.runtime.remaining_seconds(phase)
            if remaining <= 1.0:
                raise RuntimeLimitReached(
                    scope="cycle" if phase == BACKFILL_PHASE else "phase",
                    reason="request_timeout_budget",
                    phase=phase,
                )
            timeout_seconds = min(DEFAULT_FETCH_TIMEOUT_SECONDS, remaining)
            try:
                html = self.fetcher(self.target.page_url(page), timeout_seconds)
                break
            except error.HTTPError as exc:
                if exc.code in {403, 429, 430}:
                    self.runtime.block(
                        f"Blocked by FMKorea with HTTP {exc.code} on page {page}."
                    )
                raise CrawlSourceError(
                    f"FMKorea returned HTTP {exc.code} on page {page}."
                ) from exc
            except CrawlBlockedError as exc:
                self.runtime.block(str(exc))
            except CrawlTimeoutError as exc:
                last_timeout = exc
                if attempt >= self.transient_fetch_attempts:
                    raise
            except CrawlTransientError:
                if attempt >= self.transient_fetch_attempts:
                    raise
            except CrawlSourceError as exc:
                if "HTTP 430" in str(exc):
                    self.runtime.block(str(exc))
                raise
            finally:
                self.runtime.complete_request()
        else:
            raise CrawlSourceError(f"FMKorea page {page} was not fetched.")

        parser = self._parser(page)
        parser.feed(html)
        parser.close()
        if not parser.diagnostics.is_collection_safe:
            details = "; ".join(
                f"{item.code}: {item.message}"
                for item in parser.diagnostics.errors[:3]
            )
            if not details:
                details = "candidate row evidence is missing or ambiguous"
            raise CrawlSourceError(
                "FMKorea page could not be safely parsed "
                f"(page={page}, candidates={parser.diagnostics.candidate_rows}, "
                f"parsed={parser.diagnostics.parsed_rows}, "
                "unique_canonical_ids="
                f"{parser.diagnostics.has_unique_canonical_ids}): {details}"
            )
        navigation = parser.navigation
        if not navigation.is_valid:
            details = "; ".join(
                f"{item.code}: {item.message}"
                for item in navigation.errors[:3]
            )
            if not details:
                details = "pagination/current-page evidence is missing or ambiguous"
            raise CrawlSourceError(
                "FMKorea pagination could not be safely verified "
                f"(requested_page={page}, rendered_page={navigation.current_page}): "
                f"{details}"
            )
        return FmkoreaPage(
            page=page,
            posts=parser.posts,
            has_later_page=navigation.has_later_page,
            can_prove_last_page=navigation.can_prove_last_page,
        )

    def _parser(self, page: int):
        common = {
            "base_url": self.target.board_url,
            "now": self.cycle_started_at,
            "min_upvotes": self.target.min_upvotes,
            "min_comments": self.target.min_comments,
            "requested_page": page,
        }
        if self.target.collector_kind == "fmkorea-search":
            return FmkoreaSearchParser(**common)
        return FmkoreaBoardParser(**common)

    def _qualifies(self, post: FmkoreaPost) -> bool:
        return is_fmkorea_qualifying_post(
            post,
            collect_all=self.target.collect_all,
            min_upvotes=self.target.min_upvotes,
            min_comments=self.target.min_comments,
        )

    def _update_summary(
        self,
        summary: FmkoreaPhaseSummary,
        snapshot: FmkoreaPage,
    ) -> None:
        summary.scanned_pages += 1
        summary.scanned_posts += len(snapshot.posts)
        summary.pages.append(snapshot.page)
        oldest = snapshot.oldest_created_at.astimezone(timezone.utc).isoformat()
        if not summary.oldest_seen_at or parse_datetime(oldest) < parse_datetime(
            summary.oldest_seen_at
        ):
            summary.oldest_seen_at = oldest

    def _record_summary(
        self,
        summary: FmkoreaPhaseSummary,
        *,
        error_message: str = "",
    ) -> None:
        if not self.client:
            return
        try:
            record_run(
                self.client,
                target=self.target,
                status=summary.status,
                scanned_pages=summary.scanned_pages,
                scanned_posts=summary.scanned_posts,
                matched_posts=summary.matched_posts,
                run_started_at=self.run_started_at,
                error_message=error_message,
                ensure_source=False,
                run_type=summary.run_type,
            )
        except Exception as exc:
            self.persistence_warnings.append(f"Could not record FMKorea run: {exc}")

    def _record_block(self, summary: FmkoreaPhaseSummary, reason: str) -> None:
        blocked_at = self.cycle_started_at
        self.source_state.last_blocked_at = blocked_at.astimezone(timezone.utc).isoformat()
        self.source_state.last_block_reason = reason[:500]
        self.source_state.blocked_until = (
            blocked_at + timedelta(hours=self.target.block_cooldown_hours)
        ).astimezone(timezone.utc).isoformat()
        if self.client:
            try:
                save_source_state(self.client, self.source_state)
            except Exception as exc:
                self.persistence_warnings.append(
                    f"Could not save FMKorea block cooldown: {exc}"
                )
        self._record_summary(summary, error_message=reason)

    def _result(
        self,
        status: str,
        summary: Optional[FmkoreaPhaseSummary],
        error: str = "",
    ) -> Dict[str, object]:
        return {
            "target": self.target.key,
            "archive": self.target.archive_key,
            "mode": self.mode,
            "status": status,
            "persisted": bool(self.client),
            "cycle_started_at": self.run_started_at,
            "source_requests": self.runtime.request_count,
            "blocked_reason": self.runtime.blocked_reason or "",
            "persistence_warnings": self.persistence_warnings,
            "error": error,
            "phase": asdict(summary) if summary is not None else None,
        }


def run_fmkorea_target(
    target: TargetBoard,
    mode: str,
    client: Optional[D1Client] = None,
    fetcher: Optional[Callable[[str, float], str]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Stable entry point used by the all-target workflow orchestrator."""

    return FmkoreaCycle(
        target=target,
        mode=mode,
        client=client,
        fetcher=fetcher,
        now=now,
    ).run()


def parse_datetime(value: str) -> datetime:
    return ensure_aware(datetime.fromisoformat(value))


def parse_optional_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded FMKorea crawl lane.")
    parser.add_argument("--target", required=True)
    parser.add_argument("--mode", choices=VALID_CYCLE_MODES, required=True)
    parser.add_argument(
        "--persist",
        action="store_true",
        default=is_truthy(get_env("TC_PERSIST", "0")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = get_target(args.target)
    client = None
    if args.persist:
        client = D1Client(
            account_id=get_required_env("TC_CF_ACCOUNT_ID"),
            database_id=get_required_env("TC_CF_DATABASE_ID"),
            api_token=get_required_env("TC_CF_API_TOKEN"),
        )
    result = run_fmkorea_target(target, args.mode, client=client)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] in {"blocked", "failed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
