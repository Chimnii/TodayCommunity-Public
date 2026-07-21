from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from crawler.parsers.dcinside import build_qualifies_by, meets_collection_threshold


KST = timezone(timedelta(hours=9))
ASCII_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]*$")
PLAIN_COUNT = re.compile(r"^[0-9]+$")
GROUPED_COUNT = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})+$")
SIGNED_PLAIN_COUNT = re.compile(r"^-?[0-9]+$")
SIGNED_GROUPED_COUNT = re.compile(r"^-?[0-9]{1,3}(?:,[0-9]{3})+$")
BRACKETED_COUNT = re.compile(r"^\[\s*([^\[\]]+)\s*\]$")
FULL_DATETIME = re.compile(
    r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?$"
)
FULL_DATE = re.compile(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})$")
SHORT_DATE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})$")
TIME_ONLY = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
RELATIVE_TIME = re.compile(r"^(\d+)\s*(초|분|시간|일)\s*전$")
COMMENT_DATETIME = re.compile(
    r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
)
COMMENT_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)")


@dataclass(frozen=True)
class FmkoreaPost:
    external_post_id: str
    subject: str
    title: str
    post_url: str
    created_at: str
    created_at_raw: str
    upvotes: int
    comments: int
    qualifies_by: str


@dataclass(frozen=True)
class FmkoreaParseError:
    external_post_id: str
    code: str
    message: str


@dataclass
class FmkoreaParseDiagnostics:
    candidate_rows: int = 0
    parsed_rows: int = 0
    ignored_rows: int = 0
    candidate_post_ids: List[str] = field(default_factory=list)
    errors: List[FmkoreaParseError] = field(default_factory=list)
    row_container_seen: bool = False

    @property
    def failed_rows(self) -> int:
        return self.candidate_rows - self.parsed_rows

    @property
    def has_unique_canonical_ids(self) -> bool:
        return (
            len(self.candidate_post_ids) == len(set(self.candidate_post_ids))
            and len(self.candidate_post_ids) == self.parsed_rows
        )

    @property
    def is_collection_safe(self) -> bool:
        return (
            self.row_container_seen
            and self.candidate_rows > 0
            and self.failed_rows == 0
            and self.has_unique_canonical_ids
            and not self.errors
        )


@dataclass(frozen=True)
class FmkoreaNavigationError:
    code: str
    message: str


@dataclass
class FmkoreaNavigationDiagnostics:
    requested_page: Optional[int] = None
    container_count: int = 0
    closed_container_count: int = 0
    current_page_observations: List[int] = field(default_factory=list)
    linked_pages: List[int] = field(default_factory=list)
    errors: List[FmkoreaNavigationError] = field(default_factory=list)

    @property
    def current_page(self) -> Optional[int]:
        if len(self.current_page_observations) != 1:
            return None
        return self.current_page_observations[0]

    @property
    def is_valid(self) -> bool:
        return (
            self.container_count == 1
            and self.closed_container_count == 1
            and not self.errors
            and self.current_page is not None
            and (
                self.requested_page is None
                or self.current_page == self.requested_page
            )
        )

    @property
    def has_later_page(self) -> bool:
        return self.current_page is not None and any(
            page > self.current_page for page in self.linked_pages
        )

    @property
    def can_prove_last_page(self) -> bool:
        return self.is_valid and not self.has_later_page

def _class_tokens(attrs: Dict[str, str]) -> set[str]:
    return {token.casefold() for token in attrs.get("class", "").split()}


def _clean_text(parts: List[str]) -> str:
    return " ".join(" ".join(parts).split())


def parse_count(
    value: str,
    *,
    bracketed: bool = False,
    allow_negative: bool = False,
) -> int:
    cleaned = " ".join(value.split())
    if bracketed:
        match = BRACKETED_COUNT.fullmatch(cleaned)
        if not match:
            raise ValueError(f"Invalid bracketed count: {value!r}")
        cleaned = match.group(1).strip()
    plain_pattern = SIGNED_PLAIN_COUNT if allow_negative else PLAIN_COUNT
    grouped_pattern = SIGNED_GROUPED_COUNT if allow_negative else GROUPED_COUNT
    if not (plain_pattern.fullmatch(cleaned) or grouped_pattern.fullmatch(cleaned)):
        raise ValueError(f"Invalid count: {value!r}")
    return int(cleaned.replace(",", ""))


def as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _validated_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def _time_near(
    *,
    current: datetime,
    estimate: datetime,
    hour: int,
    minute: int,
    second: int,
) -> datetime:
    candidates = [
        _validated_datetime(
            (estimate + timedelta(days=offset)).year,
            (estimate + timedelta(days=offset)).month,
            (estimate + timedelta(days=offset)).day,
            hour,
            minute,
            second,
        )
        for offset in (-1, 0, 1)
    ]
    not_future = [item for item in candidates if item <= current + timedelta(minutes=1)]
    choices = not_future or candidates
    return min(choices, key=lambda item: abs((item - estimate).total_seconds()))


def normalize_fmkorea_datetime(
    raw_value: str,
    *,
    comment_value: str = "",
    now: Optional[datetime] = None,
) -> str:
    current = as_kst(now or datetime.now(KST)).replace(microsecond=0)
    cleaned = " ".join(raw_value.split())
    comment = " ".join(comment_value.split())

    full = FULL_DATETIME.fullmatch(cleaned)
    if full:
        year, month, day, hour, minute = map(int, full.groups()[:5])
        second = int(full.group(6) or 0)
        return _validated_datetime(year, month, day, hour, minute, second).isoformat()

    full_date = FULL_DATE.fullmatch(cleaned)
    if full_date:
        return _validated_datetime(
            *map(int, full_date.groups()),
            hour=23,
            minute=59,
            second=59,
        ).isoformat()

    short_date = SHORT_DATE.fullmatch(cleaned)
    if short_date:
        month, day = map(int, short_date.groups())
        candidate = _validated_datetime(
            current.year, month, day, hour=23, minute=59, second=59
        )
        if candidate > current + timedelta(days=1):
            candidate = _validated_datetime(
                current.year - 1,
                month,
                day,
                hour=23,
                minute=59,
                second=59,
            )
        return candidate.isoformat()

    time_only = TIME_ONLY.fullmatch(cleaned)
    if time_only:
        hour, minute = map(int, time_only.groups()[:2])
        second = int(time_only.group(3) or 0)
        return _time_near(
            current=current,
            estimate=current,
            hour=hour,
            minute=minute,
            second=second,
        ).isoformat()

    if cleaned in {"방금", "방금 전"}:
        estimate = current
    else:
        relative = RELATIVE_TIME.fullmatch(cleaned)
        if not relative:
            raise ValueError(f"Unsupported FMKorea datetime: {raw_value!r}")
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {
            "초": timedelta(seconds=amount),
            "분": timedelta(minutes=amount),
            "시간": timedelta(hours=amount),
            "일": timedelta(days=amount),
        }[unit]
        estimate = current - delta

    comment_datetime = COMMENT_DATETIME.search(comment)
    if comment_datetime:
        year, month, day, hour, minute = map(
            int, comment_datetime.groups()[:5]
        )
        second = int(comment_datetime.group(6) or 0)
        return _validated_datetime(year, month, day, hour, minute, second).isoformat()

    comment_time = COMMENT_TIME.search(comment)
    if comment_time:
        hour, minute = map(int, comment_time.groups()[:2])
        second = int(comment_time.group(3) or 0)
        return _time_near(
            current=current,
            estimate=estimate,
            hour=hour,
            minute=minute,
            second=second,
        ).isoformat()
    return estimate.isoformat()


def validate_search_datetime_evidence(
    raw_value: str,
    *,
    comment_value: str,
    now: datetime,
) -> None:
    """Require stable hidden clock evidence for a relative search timestamp."""

    cleaned = " ".join(raw_value.split())
    relative = RELATIVE_TIME.fullmatch(cleaned)
    if cleaned not in {"방금", "방금 전"} and relative is None:
        return
    comment = " ".join(comment_value.split())
    if not (COMMENT_DATETIME.search(comment) or COMMENT_TIME.search(comment)):
        raise ValueError(
            "Relative search datetime is missing its hidden exact-time comment."
        )

    # A hidden clock from the wrong row can still look syntactically valid.
    # Verify that the reconstructed instant is reasonably close to the visible
    # relative label, including when it crosses midnight.
    normalized = datetime.fromisoformat(
        normalize_fmkorea_datetime(cleaned, comment_value=comment, now=now)
    )
    current = as_kst(now)
    if relative is None:
        estimate = current
        tolerance = timedelta(minutes=2)
    else:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {
            "초": timedelta(seconds=amount),
            "분": timedelta(minutes=amount),
            "시간": timedelta(hours=amount),
            "일": timedelta(days=amount),
        }[unit]
        estimate = current - delta
        tolerance = {
            "초": timedelta(minutes=2),
            "분": timedelta(minutes=2),
            "시간": timedelta(hours=1, minutes=2),
            "일": timedelta(days=1, minutes=2),
        }[unit]
    if abs(normalized - estimate) > tolerance:
        raise ValueError(
            "Relative search datetime and hidden exact-time comment disagree."
        )


def _origin(url: str) -> Tuple[str, str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("FMKorea base URL must be an absolute HTTP(S) URL.")
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def _document_id(parsed_url) -> str:
    query = parse_qs(parsed_url.query, keep_blank_values=True)
    query_values = query.get("document_srl", [])
    query_id = ""
    if query_values:
        if len(query_values) != 1 or not ASCII_POSITIVE_INTEGER.fullmatch(
            query_values[0].strip()
        ):
            raise ValueError("Post link has an invalid or ambiguous document_srl.")
        query_id = query_values[0].strip()

    path_match = re.search(r"/([1-9][0-9]*)/?$", parsed_url.path)
    path_id = path_match.group(1) if path_match else ""
    if query_id and path_id and query_id != path_id:
        raise ValueError("Post link path id and document_srl do not match.")
    document_id = query_id or path_id
    if not document_id:
        raise ValueError("Post link does not contain a canonical document id.")
    return document_id


def validate_post_url(base_url: str, href: str) -> tuple[str, str]:
    resolved = urlparse(urljoin(base_url, href.strip()))
    if _origin(resolved.geturl()) != _origin(base_url):
        raise ValueError("Post link must use the same origin as the target feed.")
    if resolved.username or resolved.password:
        raise ValueError("Post link must not contain credentials.")
    document_id = _document_id(resolved)
    canonical = urlunparse(
        (
            resolved.scheme,
            resolved.netloc,
            resolved.path,
            resolved.params,
            resolved.query,
            "",
        )
    )
    return document_id, canonical


def is_fmkorea_qualifying_post(
    post: FmkoreaPost,
    *,
    collect_all: bool,
    min_upvotes: int,
    min_comments: int,
) -> bool:
    if collect_all:
        return True
    return meets_collection_threshold(
        post.upvotes,
        post.comments,
        min_upvotes=min_upvotes,
        min_comments=min_comments,
    )


class _FmkoreaParserBase(HTMLParser):
    requires_auxiliary_vote_link = False

    row_tag = ""

    def __init__(
        self,
        *,
        base_url: str,
        now: Optional[datetime] = None,
        min_upvotes: int = 1,
        min_comments: int = 1,
        collect_all: bool = False,
        requested_page: Optional[int] = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        _origin(base_url)
        if not collect_all and (min_upvotes <= 0 or min_comments <= 0):
            raise ValueError("Collection thresholds must be positive integers.")
        self.base_url = base_url
        self.now = as_kst(now or datetime.now(KST))
        self.min_upvotes = min_upvotes
        self.min_comments = min_comments
        self.collect_all = collect_all
        self.posts: List[FmkoreaPost] = []
        self.diagnostics = FmkoreaParseDiagnostics()
        if requested_page is not None and (
            isinstance(requested_page, bool) or int(requested_page) < 1
        ):
            raise ValueError("requested_page must be a positive integer")
        self.navigation = FmkoreaNavigationDiagnostics(
            requested_page=int(requested_page) if requested_page is not None else None
        )
        self.current_row: Optional[Dict[str, object]] = None
        self._row_depth = 0
        self._eof_checked = False
        self._navigation_container_tag = ""
        self._navigation_container_depth = 0
        self._navigation_anchor: Optional[Dict[str, object]] = None
        self._navigation_current_tag = ""
        self._navigation_current_parts: List[str] = []
        self._expected_feed_query = parse_qs(
            urlparse(base_url).query,
            keep_blank_values=True,
        )
        self._expected_feed_query.pop("page", None)

    def close(self) -> None:
        super().close()
        if self._eof_checked:
            return
        self._eof_checked = True
        if self._navigation_container_depth:
            self.navigation.errors.append(
                FmkoreaNavigationError(
                    "unterminated_pagination",
                    "Pagination container did not close before end of input.",
                )
            )
            self._navigation_container_depth = 0
            self._navigation_container_tag = ""
        if self._navigation_anchor is not None:
            self.navigation.errors.append(
                FmkoreaNavigationError(
                    "unterminated_pagination_link",
                    "Pagination link did not close before end of input.",
                )
            )
            self._navigation_anchor = None
        if self.current_row is not None:
            self._error(
                self.current_row,
                "unterminated_candidate_row",
                "Candidate row did not close before end of input.",
            )
            self.current_row = None
            self._row_depth = 0
        duplicate_ids = sorted(
            {
                post_id
                for post_id in self.diagnostics.candidate_post_ids
                if self.diagnostics.candidate_post_ids.count(post_id) > 1
            }
        )
        for post_id in duplicate_ids:
            self.diagnostics.errors.append(
                FmkoreaParseError(
                    post_id,
                    "duplicate_document_id",
                    "The same canonical document id appeared more than once.",
                )
            )

    def _new_row(self, attrs: Dict[str, str], *, candidate: bool) -> Dict[str, object]:
        return {
            "candidate": candidate,
            "expected_ids": [
                value.strip()
                for name, value in attrs.items()
                if name.casefold() in {"data-document-srl", "data-document_srl"}
                and value.strip()
            ],
            "title_hrefs": [],
            "title_parts": [],
            "title_link_count": 0,
            "vote_link_count": 0,
            "vote_href_ids": [],
            "subject_parts": [],
            "date_parts": [],
            "date_comments": [],
            "upvote_parts": [],
            "comment_parts": [],
            "comment_observations": 0,
        }

    def _handle_navigation_starttag(
        self,
        tag: str,
        attrs: Dict[str, str],
    ) -> bool:
        tokens = _class_tokens(attrs)
        if not self._navigation_container_depth:
            if tag not in {"div", "nav"} or "pagination" not in tokens:
                return False
            self.navigation.container_count += 1
            self._navigation_container_tag = tag
            self._navigation_container_depth = 1
            return True

        if tag == self._navigation_container_tag:
            self._navigation_container_depth += 1
        if tag == "a":
            if self._navigation_anchor is not None:
                self.navigation.errors.append(
                    FmkoreaNavigationError(
                        "nested_pagination_link",
                        "Pagination links must not be nested.",
                    )
                )
            self._navigation_anchor = {
                "href": attrs.get("href", "").strip(),
                "parts": [],
            }
            if "current" in tokens or "active" in tokens:
                self._navigation_current_tag = "a"
                self._navigation_current_parts = []
        elif tag in {"strong", "em", "span"} and (
            tag == "strong" or "current" in tokens or "active" in tokens
        ):
            if self._navigation_current_tag:
                self.navigation.errors.append(
                    FmkoreaNavigationError(
                        "ambiguous_current_page",
                        "Pagination exposes more than one current-page marker.",
                    )
                )
            self._navigation_current_tag = tag
            self._navigation_current_parts = []
        return True

    def _handle_navigation_data(self, data: str) -> bool:
        if not self._navigation_container_depth:
            return False
        cleaned = " ".join(data.split())
        if cleaned:
            if self._navigation_anchor is not None:
                self._navigation_anchor["parts"].append(cleaned)
            if self._navigation_current_tag:
                self._navigation_current_parts.append(cleaned)
        return True

    def _handle_navigation_endtag(self, tag: str) -> bool:
        if not self._navigation_container_depth:
            return False
        if tag == self._navigation_current_tag:
            self._record_current_page()
            self._navigation_current_tag = ""
            self._navigation_current_parts = []
        if tag == "a" and self._navigation_anchor is not None:
            self._record_pagination_link(self._navigation_anchor)
            self._navigation_anchor = None
        if tag == self._navigation_container_tag:
            self._navigation_container_depth -= 1
            if self._navigation_container_depth == 0:
                self.navigation.closed_container_count += 1
                self._navigation_container_tag = ""
        return True

    def _record_current_page(self) -> None:
        value = _clean_text(self._navigation_current_parts)
        if not ASCII_POSITIVE_INTEGER.fullmatch(value):
            self.navigation.errors.append(
                FmkoreaNavigationError(
                    "invalid_current_page",
                    f"Current pagination marker is not a positive integer: {value!r}.",
                )
            )
            return
        self.navigation.current_page_observations.append(int(value))

    def _record_pagination_link(self, capture: Dict[str, object]) -> None:
        href = str(capture["href"])
        text_value = _clean_text(list(capture["parts"]))
        text_page = int(text_value) if ASCII_POSITIVE_INTEGER.fullmatch(text_value) else None
        resolved = urlparse(urljoin(self.base_url, href))
        query = parse_qs(resolved.query, keep_blank_values=True)
        page_values = query.get("page", [])
        if not page_values and text_page == 1:
            page_values = ["1"]
        if not page_values:
            return
        if _origin(resolved.geturl()) != _origin(self.base_url):
            self.navigation.errors.append(
                FmkoreaNavigationError(
                    "invalid_pagination_origin",
                    "Pagination link must use the same origin as the feed.",
                )
            )
            return
        if len(page_values) != 1 or not ASCII_POSITIVE_INTEGER.fullmatch(
            page_values[0].strip()
        ):
            self.navigation.errors.append(
                FmkoreaNavigationError(
                    "invalid_pagination_page",
                    "Pagination page parameter is invalid or ambiguous.",
                )
            )
            return
        page = int(page_values[0])
        if text_page is not None and text_page != page:
            self.navigation.errors.append(
                FmkoreaNavigationError(
                    "pagination_page_mismatch",
                    "Pagination link text and page parameter do not match.",
                )
            )
            return
        for key, expected_values in self._expected_feed_query.items():
            if query.get(key, []) != expected_values:
                self.navigation.errors.append(
                    FmkoreaNavigationError(
                        "pagination_feed_mismatch",
                        f"Pagination link does not preserve feed parameter {key!r}.",
                    )
                )
                return
        self.navigation.linked_pages.append(page)

    def _mark_candidate(self, row: Dict[str, object]) -> None:
        if not row["candidate"]:
            row["candidate"] = True
            self.diagnostics.candidate_rows += 1

    def _error(
        self,
        row: Dict[str, object],
        code: str,
        message: str,
        external_post_id: str = "",
    ) -> FmkoreaParseError:
        if not external_post_id:
            expected_ids = row.get("expected_ids", [])
            external_post_id = str(expected_ids[0]) if expected_ids else ""
        error = FmkoreaParseError(external_post_id, code, message)
        self.diagnostics.errors.append(error)
        return error

    def _finish_row(self) -> None:
        row = self.current_row
        self.current_row = None
        self._row_depth = 0
        if not row:
            return
        if not row["candidate"]:
            self.diagnostics.ignored_rows += 1
            return

        post = self._build_post(row)
        if post is None:
            return
        self.posts.append(post)
        self.diagnostics.parsed_rows += 1
        self.diagnostics.candidate_post_ids.append(post.external_post_id)

    def _build_post(self, row: Dict[str, object]) -> Optional[FmkoreaPost]:
        hrefs = list(row["title_hrefs"])
        if int(row["title_link_count"]) != 1 or len(hrefs) != 1:
            self._error(
                row,
                "ambiguous_title_link",
                "Candidate row must contain exactly one canonical title link.",
            )
            return None
        try:
            external_post_id, post_url = validate_post_url(self.base_url, str(hrefs[0]))
        except ValueError as exc:
            self._error(row, "invalid_post_link", str(exc))
            return None

        if self.requires_auxiliary_vote_link and (
            int(row["vote_link_count"]) != 1
            or len(list(row["vote_href_ids"])) != 1
        ):
            self._error(
                row,
                "invalid_auxiliary_link",
                "Search rows must contain exactly one valid same-origin vote link.",
                external_post_id,
            )
            return None

        observed_ids = [str(value) for value in row["expected_ids"]]
        observed_ids.extend(str(value) for value in row["vote_href_ids"])
        if any(
            not ASCII_POSITIVE_INTEGER.fullmatch(value)
            or value != external_post_id
            for value in observed_ids
        ):
            self._error(
                row,
                "document_id_mismatch",
                "Row and auxiliary-link document ids must match the title link id.",
                external_post_id,
            )
            return None

        title = _clean_text(list(row["title_parts"]))
        subject = _clean_text(list(row["subject_parts"]))
        created_at_raw = _clean_text(list(row["date_parts"]))
        if not title:
            self._error(row, "missing_title", "Candidate title is empty.", external_post_id)
            return None
        if not subject:
            self._error(
                row,
                "missing_subject",
                "Candidate category/subject is empty.",
                external_post_id,
            )
            return None
        if not created_at_raw:
            self._error(
                row,
                "missing_datetime",
                "Candidate datetime is empty.",
                external_post_id,
            )
            return None

        try:
            upvotes = parse_count(
                _clean_text(list(row["upvote_parts"])),
                allow_negative=True,
            )
            comments = self._comment_count(row)
            self._validate_datetime_evidence(
                created_at_raw,
                " ".join(str(item) for item in row["date_comments"]),
            )
            created_at = normalize_fmkorea_datetime(
                created_at_raw,
                comment_value=" ".join(str(item) for item in row["date_comments"]),
                now=self.now,
            )
        except ValueError as exc:
            self._error(row, "invalid_candidate_metric", str(exc), external_post_id)
            return None

        qualifies_by = (
            "keyword"
            if self.collect_all
            else build_qualifies_by(
                upvotes,
                comments,
                min_upvotes=self.min_upvotes,
                min_comments=self.min_comments,
            )
        )
        return FmkoreaPost(
            external_post_id=external_post_id,
            subject=subject,
            title=title,
            post_url=post_url,
            created_at=created_at,
            created_at_raw=created_at_raw,
            upvotes=upvotes,
            comments=comments,
            qualifies_by=qualifies_by,
        )

    def _comment_count(self, row: Dict[str, object]) -> int:
        observations = int(row["comment_observations"])
        if observations == 0:
            return 0
        if observations != 1:
            raise ValueError("Candidate has an ambiguous comment count.")
        return parse_count(
            _clean_text(list(row["comment_parts"])),
            bracketed=self._comments_are_bracketed(),
        )

    def _comments_are_bracketed(self) -> bool:
        raise NotImplementedError

    def _validate_datetime_evidence(
        self,
        raw_value: str,
        comment_value: str,
    ) -> None:
        return None


class FmkoreaSearchParser(_FmkoreaParserBase):
    requires_auxiliary_vote_link = True

    row_tag = "li"

    def __init__(self, **kwargs) -> None:
        kwargs["collect_all"] = True
        super().__init__(**kwargs)
        self._title_h3_depth = 0
        self._title_anchor_depth = 0
        self._title_capture_depth = 0
        self._vote_anchor_depth = 0
        self._vote_capture_depth = 0
        self._comment_capture_depth = 0
        self._subject_capture_depth = 0
        self._date_capture_depth = 0

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = {name: value or "" for name, value in attrs_list}
        if self._handle_navigation_starttag(tag, attrs):
            return
        tokens = _class_tokens(attrs)
        if self.current_row is None:
            if tag != "li" or "li_best2" not in tokens:
                return
            self.current_row = self._new_row(attrs, candidate=True)
            self._row_depth = 1
            self.diagnostics.row_container_seen = True
            self.diagnostics.candidate_rows += 1
            return

        row = self.current_row
        if tag == "li":
            self._row_depth += 1
        if tag == "h3" and "title" in tokens:
            self._title_h3_depth += 1
        elif tag == "a" and self._title_h3_depth:
            self._title_anchor_depth += 1
            row["title_link_count"] += 1
            row["title_hrefs"].append(attrs.get("href", ""))
        elif tag == "span" and self._title_anchor_depth and "ellipsis-target" in tokens:
            self._title_capture_depth += 1
        elif tag == "a" and "pc_voted_count" in tokens:
            self._vote_anchor_depth += 1
            row["vote_link_count"] += 1
            self._observe_auxiliary_href(row, attrs.get("href", ""))
        elif tag == "span" and self._vote_anchor_depth and "count" in tokens:
            self._vote_capture_depth += 1
        elif tag == "span" and "comment_count" in tokens:
            self._comment_capture_depth += 1
            row["comment_observations"] += 1
        elif tag == "span" and "category" in tokens:
            self._subject_capture_depth += 1
        elif tag == "span" and "regdate" in tokens:
            self._date_capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._handle_navigation_endtag(tag):
            return
        if self.current_row is None:
            return
        if tag == "span":
            if self._title_capture_depth:
                self._title_capture_depth -= 1
            elif self._vote_capture_depth:
                self._vote_capture_depth -= 1
            elif self._comment_capture_depth:
                self._comment_capture_depth -= 1
            elif self._subject_capture_depth:
                self._subject_capture_depth -= 1
            elif self._date_capture_depth:
                self._date_capture_depth -= 1
        elif tag == "a":
            if self._title_anchor_depth:
                self._title_anchor_depth -= 1
            elif self._vote_anchor_depth:
                self._vote_anchor_depth -= 1
        elif tag == "h3" and self._title_h3_depth:
            self._title_h3_depth -= 1
        elif tag == "li":
            self._row_depth -= 1
            if self._row_depth == 0:
                self._finish_row()
                self._reset_captures()

    def handle_data(self, data: str) -> None:
        if self._handle_navigation_data(data):
            return
        row = self.current_row
        if row is None:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._title_capture_depth:
            row["title_parts"].append(cleaned)
        elif self._vote_capture_depth:
            row["upvote_parts"].append(cleaned)
        elif self._comment_capture_depth:
            row["comment_parts"].append(cleaned)
        elif self._subject_capture_depth:
            row["subject_parts"].append(cleaned)
        elif self._date_capture_depth:
            row["date_parts"].append(cleaned)

    def handle_comment(self, data: str) -> None:
        if self.current_row is not None and self._date_capture_depth:
            self.current_row["date_comments"].append(data.strip())

    def _observe_auxiliary_href(self, row: Dict[str, object], href: str) -> None:
        try:
            document_id, _ = validate_post_url(self.base_url, href)
        except ValueError:
            return
        row["vote_href_ids"].append(document_id)

    def _comments_are_bracketed(self) -> bool:
        return True

    def _validate_datetime_evidence(
        self,
        raw_value: str,
        comment_value: str,
    ) -> None:
        validate_search_datetime_evidence(
            raw_value,
            comment_value=comment_value,
            now=self.now,
        )

    def _reset_captures(self) -> None:
        self._title_h3_depth = 0
        self._title_anchor_depth = 0
        self._title_capture_depth = 0
        self._vote_anchor_depth = 0
        self._vote_capture_depth = 0
        self._comment_capture_depth = 0
        self._subject_capture_depth = 0
        self._date_capture_depth = 0


class FmkoreaBoardParser(_FmkoreaParserBase):
    row_tag = "tr"

    def __init__(self, **kwargs) -> None:
        kwargs["collect_all"] = False
        super().__init__(**kwargs)
        self._skip_row = False
        self._cell = ""
        self._title_capture_depth = 0
        self._comment_capture_depth = 0

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = {name: value or "" for name, value in attrs_list}
        if self._handle_navigation_starttag(tag, attrs):
            return
        tokens = _class_tokens(attrs)
        if self.current_row is None:
            if tag != "tr":
                return
            self.current_row = self._new_row(attrs, candidate=False)
            self._row_depth = 1
            self._skip_row = any("notice" in token for token in tokens)
            self.diagnostics.row_container_seen = True
            return

        row = self.current_row
        if tag == "tr":
            self._row_depth += 1
        elif tag == "td":
            if "cate" in tokens:
                self._cell = "subject"
            elif "title" in tokens:
                self._cell = "title"
                if not self._skip_row:
                    self._mark_candidate(row)
            elif "time" in tokens:
                self._cell = "date"
            elif "m_no_voted" in tokens:
                self._cell = "upvotes"
            else:
                self._cell = ""
        elif tag == "a" and self._cell == "title" and "hx" in tokens:
            if not self._skip_row:
                self._mark_candidate(row)
                self._title_capture_depth += 1
                row["title_link_count"] += 1
                row["title_hrefs"].append(attrs.get("href", ""))
        elif tag == "a" and self._cell == "title" and "replynum" in tokens:
            if not self._skip_row:
                self._comment_capture_depth += 1
                row["comment_observations"] += 1

    def handle_endtag(self, tag: str) -> None:
        if self._handle_navigation_endtag(tag):
            return
        if self.current_row is None:
            return
        if tag == "a":
            if self._comment_capture_depth:
                self._comment_capture_depth -= 1
            elif self._title_capture_depth:
                self._title_capture_depth -= 1
        elif tag == "td":
            self._cell = ""
        elif tag == "tr":
            self._row_depth -= 1
            if self._row_depth == 0:
                if self._skip_row:
                    self.current_row["candidate"] = False
                self._finish_row()
                self._reset_captures()

    def handle_data(self, data: str) -> None:
        if self._handle_navigation_data(data):
            return
        row = self.current_row
        if row is None or self._skip_row:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._comment_capture_depth:
            row["comment_parts"].append(cleaned)
        elif self._title_capture_depth:
            row["title_parts"].append(cleaned)
        elif self._cell == "subject":
            row["subject_parts"].append(cleaned)
        elif self._cell == "date":
            row["date_parts"].append(cleaned)
        elif self._cell == "upvotes":
            row["upvote_parts"].append(cleaned)

    def _comments_are_bracketed(self) -> bool:
        return False

    def _comment_count(self, row: Dict[str, object]) -> int:
        observations = int(row["comment_observations"])
        if observations == 0:
            return 0
        if observations != 1:
            raise ValueError("Candidate has an ambiguous comment count.")
        value = _clean_text(list(row["comment_parts"]))
        return parse_count(value, bracketed=value.startswith("["))

    def _reset_captures(self) -> None:
        self._skip_row = False
        self._cell = ""
        self._title_capture_depth = 0
        self._comment_capture_depth = 0
