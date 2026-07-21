from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse


KST = timezone(timedelta(hours=9))
POST_PATH_SEGMENT = "/mgallery/board/view/"
ASCII_POST_ID_PATTERN = re.compile(r"^[0-9]+$")
PLAIN_COUNT_PATTERN = re.compile(r"^[0-9]+$")
GROUPED_COUNT_PATTERN = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})+$")
COMMENT_BRACKET_PATTERN = re.compile(r"^\[([^\[\]]+)\]$")
COMMENT_COUNT_PAIR_PATTERN = re.compile(
    r"^([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)/"
    r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$"
)
FULL_DATETIME_PATTERN = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2}):(\d{1,2})$"
)
TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{1,2})$")
SHORT_DATE_PATTERN = re.compile(r"^(\d{2})[./](\d{1,2})[./](\d{1,2})$")
FULL_DATE_PATTERN = re.compile(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$")
DEFAULT_MIN_UPVOTES = 4
DEFAULT_MIN_COMMENTS = 20
PAGING_CONTAINER_CLASS = "bottom_paging_box"
PAGE_NEXT_CLASS = "page_next"
PAGE_END_CLASS = "page_end"
PAGE_NEXT_TEXT = "\ub2e4\uc74c"
PAGE_END_TEXT = "\ub05d"
SURVEY_SUBJECT_LABELS = {"\uc124\ubb38"}


@dataclass
class DcinsidePost:
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
class DcinsideParseError:
    external_post_id: str
    code: str
    message: str


@dataclass
class DcinsideParseDiagnostics:
    """Page-level facts needed before a caller advances crawl coverage."""

    total_content_rows: int = 0
    notice_rows: int = 0
    non_numeric_rows: int = 0
    parsed_rows: int = 0
    candidate_post_ids: List[str] = field(default_factory=list)
    errors: List[DcinsideParseError] = field(default_factory=list)

    @property
    def candidate_rows(self) -> int:
        return len(self.candidate_post_ids)

    @property
    def failed_rows(self) -> int:
        return self.candidate_rows - self.parsed_rows

    @property
    def is_complete(self) -> bool:
        return self.parsed_rows == self.candidate_rows and not self.errors

    @property
    def ids_strictly_descending(self) -> bool:
        return are_post_ids_strictly_descending(self.candidate_post_ids)

    @property
    def ids_coverage_ordered(self) -> bool:
        return are_post_ids_coverage_ordered(self.candidate_post_ids)

    @property
    def has_unique_canonical_ids(self) -> bool:
        numeric_ids = _canonical_post_ids(self.candidate_post_ids)
        return (
            numeric_ids is not None
            and len(numeric_ids) == len(set(numeric_ids))
        )

    @property
    def is_collection_safe(self) -> bool:
        # Preserve every fully parsed canonical post even when an unfamiliar
        # non-numeric table row is present. Such rows remain diagnostics errors
        # and therefore cannot make the page authoritative coverage evidence.
        return (
            self.candidate_rows > 0
            and self.failed_rows == 0
            and self.has_unique_canonical_ids
            and all(error.code == "non_numeric_post_id" for error in self.errors)
        )

    @property
    def is_coverage_safe(self) -> bool:
        return (
            self.is_collection_safe
            and self.is_complete
            and self.ids_coverage_ordered
        )


@dataclass(frozen=True)
class DcinsideNavigationError:
    code: str
    message: str


@dataclass
class DcinsideNavigationDiagnostics:
    """Fail-safe pagination evidence, independent from post-row completeness."""

    requested_page: Optional[int] = None
    expected_board_id: Optional[str] = None
    paging_container_seen: bool = False
    paging_container_count: int = 0
    paging_container_closed_count: int = 0
    current_page_observations: List[int] = field(default_factory=list)
    next_page_observations: List[int] = field(default_factory=list)
    last_page_observations: List[int] = field(default_factory=list)
    observed_board_ids: List[str] = field(default_factory=list)
    errors: List[DcinsideNavigationError] = field(default_factory=list)

    @property
    def current_page(self) -> Optional[int]:
        return _single_observation(self.current_page_observations)

    @property
    def next_page(self) -> Optional[int]:
        return _single_observation(self.next_page_observations)

    @property
    def last_page(self) -> Optional[int]:
        return _single_observation(self.last_page_observations)

    @property
    def has_next(self) -> bool:
        return self.next_page is not None

    @property
    def is_valid(self) -> bool:
        if (
            not self.paging_container_seen
            or self.paging_container_count < 1
            or self.paging_container_closed_count != self.paging_container_count
            or self.errors
            or self.current_page is None
        ):
            return False
        return (
            (self.requested_page is None or self.current_page == self.requested_page)
            and navigation_page_relationships_are_valid(self)
        )

    @property
    def can_prove_last_page(self) -> bool:
        return (
            self.is_valid
            and self.last_page is not None
            and self.current_page == self.last_page
            and not self.has_next
        )


class DcinsideListParser(HTMLParser):
    def __init__(
        self,
        base_url: str,
        now: Optional[datetime] = None,
        min_upvotes: int = DEFAULT_MIN_UPVOTES,
        min_comments: int = DEFAULT_MIN_COMMENTS,
        requested_page: Optional[int] = None,
        expected_board_id: Optional[str] = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._post_board_id = board_id_from_url(base_url)
        self.now = as_kst(now or datetime.now(KST))
        self.min_upvotes = min_upvotes
        self.min_comments = min_comments
        self.posts: List[DcinsidePost] = []
        self.diagnostics = DcinsideParseDiagnostics()
        normalized_requested_page, requested_page_error = normalize_optional_page(
            requested_page
        )
        normalized_board_id, board_id_error = normalize_expected_board_id(
            base_url, expected_board_id
        )
        self.navigation = DcinsideNavigationDiagnostics(
            requested_page=normalized_requested_page,
            expected_board_id=normalized_board_id,
        )
        if requested_page_error:
            self._add_navigation_error("invalid_requested_page", requested_page_error)
        if board_id_error:
            self._add_navigation_error("invalid_expected_board_id", board_id_error)
        self.current_row: Optional[Dict[str, object]] = None
        self.current_cell: Optional[str] = None
        self.capture_title = False
        self.capture_reply = False
        self._reply_capture_depth = 0
        self._reply_text_parts: List[str] = []
        self._paging_depth = 0
        self._navigation_capture: Optional[Dict[str, object]] = None
        self._navigation_eof_checked = False

    @property
    def navigation_diagnostics(self) -> DcinsideNavigationDiagnostics:
        return self.navigation

    def close(self) -> None:
        super().close()
        if self._navigation_eof_checked:
            return
        self._navigation_eof_checked = True
        if self._navigation_capture is not None:
            self._add_navigation_error(
                "unterminated_navigation_element_at_eof",
                "Pagination current/link element was still open at end of input.",
            )
            self._navigation_capture = None
        if self._paging_depth > 0:
            self._add_navigation_error(
                "unterminated_paging_container",
                "Pagination container was still open at end of input.",
            )
            self._paging_depth = 0
        self._record_navigation_relationship_errors()

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = dict(attrs_list)

        if self._handle_navigation_starttag(tag, attrs):
            return

        if tag == "tr":
            row_class = attrs.get("class", "")
            data_no = attrs.get("data-no", "").strip()
            if "ub-content" not in row_class:
                return

            self.diagnostics.total_content_rows += 1
            data_type = attrs.get("data-type", "").strip()
            if data_type == "icon_notice":
                self.diagnostics.notice_rows += 1
                self.current_row = None
                return

            if not ASCII_POST_ID_PATTERN.fullmatch(data_no):
                self.diagnostics.non_numeric_rows += 1
                self.current_row = {
                    "row_kind": "non_numeric",
                    "external_post_id": data_no,
                    "data_type": data_type,
                    "row_class_tokens": set(row_class.split()),
                    "subject_text_parts": [],
                    "title_link_count": 0,
                    "normal_view_link_seen": False,
                    "survey_signal_seen": data_no.casefold() in {"survey", "\uc124\ubb38"},
                    "interview_signal_seen": False,
                    "advertisement_icon_count": 0,
                }
                return

            self.diagnostics.candidate_post_ids.append(data_no)
            self.current_row = {
                "row_kind": "candidate",
                "external_post_id": data_no,
                "subject_cell_count": 0,
                "subject_text_parts": [],
                "subject_inner_count": 0,
                "subject_inner_text_parts": [],
                "title_parts": [],
                "post_link_attempts": 0,
                "post_urls": [],
                "post_link_errors": [],
                "created_at_raw": "",
                "created_at_title": "",
                "date_cell_count": 0,
                "recommend_cell_count": 0,
                "recommend_text_parts": [],
                "comment_markup_seen": False,
                "comment_observations": [],
                "comment_errors": [],
            }
            return

        if not self.current_row:
            return

        if tag == "td":
            td_class_tokens = set(attrs.get("class", "").split())
            if "gall_subject" in td_class_tokens:
                self.current_cell = "subject"
                if self.current_row.get("row_kind") == "candidate":
                    self.current_row["subject_cell_count"] += 1
            elif "gall_tit" in td_class_tokens:
                self.current_cell = "title"
            elif "gall_date" in td_class_tokens:
                self.current_cell = "date"
                if self.current_row.get("row_kind") == "candidate":
                    self.current_row["date_cell_count"] += 1
                    self.current_row["created_at_title"] = attrs.get("title", "").strip()
            elif "gall_recommend" in td_class_tokens:
                self.current_cell = "recommend"
                if self.current_row.get("row_kind") == "candidate":
                    self.current_row["recommend_cell_count"] += 1
            else:
                self.current_cell = None
            return

        if (
            tag == "p"
            and self.current_row.get("row_kind") == "candidate"
            and self.current_cell == "subject"
            and "subject_inner" in set(attrs.get("class", "").split())
        ):
            self.current_cell = "subject_inner"
            self.current_row["subject_inner_count"] += 1
            return

        if (
            self.current_row.get("row_kind") == "candidate"
            and self.current_cell == "title"
            and not self.capture_title
        ):
            class_tokens = set(attrs.get("class", "").split())
            if any("reply" in token.casefold() for token in class_tokens):
                self.current_row["comment_markup_seen"] = True

        if tag == "a" and self.current_cell == "title":
            href = attrs.get("href", "").strip()
            if self.current_row.get("row_kind") == "non_numeric":
                self._inspect_non_numeric_link(self.current_row, href)
            else:
                self._inspect_candidate_title_link(self.current_row, href)
            return

        if (
            tag == "em"
            and self.current_row.get("row_kind") == "non_numeric"
            and self.current_cell == "title"
            and set(attrs.get("class", "").split()) == {"icon_img", "icon_ad"}
        ):
            self.current_row["advertisement_icon_count"] += 1
            return

        if tag == "span" and self.current_row.get("row_kind") == "candidate":
            if self.capture_reply:
                self._reply_capture_depth += 1
            elif self.current_cell == "title" and "reply_num" in set(
                attrs.get("class", "").split()
            ):
                self.capture_reply = True
                self._reply_capture_depth = 1
                self._reply_text_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._handle_navigation_endtag(tag):
            return

        if tag == "span" and self.capture_reply:
            self._reply_capture_depth -= 1
            if self._reply_capture_depth == 0:
                self._observe_comment_token(
                    self.current_row,
                    " ".join(self._reply_text_parts).strip(),
                    source="reply_num",
                )
                self.capture_reply = False
                self._reply_text_parts = []
            return

        if tag == "p" and self.current_cell == "subject_inner":
            self.current_cell = "subject"
        elif tag == "a":
            self.capture_title = False
        elif tag == "td":
            self.current_cell = None
        elif tag == "tr" and self.current_row:
            if self.capture_reply:
                self.current_row["comment_errors"].append(
                    self._row_error(
                        self.current_row,
                        "unterminated_comment_count",
                        "reply_num markup did not close before the row ended.",
                    )
                )
            if self.current_row.get("row_kind") == "non_numeric":
                self._finish_non_numeric_row(self.current_row)
            else:
                post, parse_error = self._build_post(self.current_row)
                if post:
                    self.posts.append(post)
                    self.diagnostics.parsed_rows += 1
                elif parse_error:
                    self.diagnostics.errors.append(parse_error)
            self._reset_row_state()

    def handle_data(self, data: str) -> None:
        if self._handle_navigation_data(data):
            return

        if not self.current_row:
            return

        cleaned = " ".join(data.split())
        if not cleaned:
            return

        if self.capture_title:
            self.current_row["title_parts"].append(cleaned)
            return

        if self.capture_reply:
            self._reply_text_parts.append(cleaned)
            return

        if self.current_row.get("row_kind") == "non_numeric":
            if self.current_cell == "subject":
                self.current_row["subject_text_parts"].append(cleaned)
            return

        if self.current_row.get("row_kind") != "candidate":
            return

        if self.current_cell == "subject":
            self.current_row["subject_text_parts"].append(cleaned)
        elif self.current_cell == "subject_inner":
            self.current_row["subject_inner_text_parts"].append(cleaned)
        elif self.current_cell == "title" and ("[" in cleaned or "]" in cleaned):
            self._observe_comment_token(self.current_row, cleaned, source="title_bracket")
        elif self.current_cell == "date":
            self.current_row["created_at_raw"] = cleaned
        elif self.current_cell == "recommend":
            self.current_row["recommend_text_parts"].append(cleaned)

    def _inspect_non_numeric_link(self, row: Dict[str, object], href: str) -> None:
        row["title_link_count"] += 1
        if not href:
            return
        resolved = urlparse(urljoin(self.base_url, href))
        if resolved.path.rstrip("/") == POST_PATH_SEGMENT.rstrip("/"):
            row["normal_view_link_seen"] = True
        host = (resolved.hostname or "").casefold()
        path = resolved.path.casefold()
        if "survey" in host or "survey" in path or host == "event.dcinside.com":
            row["survey_signal_seen"] = True
        if (
            resolved.scheme == "http"
            and resolved.netloc == "gall.dcinside.com"
            and resolved.path == "/list.php"
            and not resolved.params
            and re.fullmatch(
                r"id=dcinterview&no=[1-9][0-9]*",
                resolved.query,
            )
            and not resolved.fragment
        ):
            row["interview_signal_seen"] = True

    def _inspect_candidate_title_link(self, row: Dict[str, object], href: str) -> None:
        if not href:
            return
        resolved = urlparse(urljoin(self.base_url, href))
        if resolved.path.rstrip("/") != POST_PATH_SEGMENT.rstrip("/"):
            return
        query = parse_qs(resolved.query, keep_blank_values=True)
        if query.get("t") == ["cv"]:
            return

        row["post_link_attempts"] += 1
        self.capture_title = True
        post_url, parse_error = self._validated_post_link(
            href, str(row["external_post_id"])
        )
        if parse_error:
            row["post_link_errors"].append(parse_error)
        elif post_url:
            row["post_urls"].append(post_url)

    def _validated_post_link(
        self, href: str, external_post_id: str
    ) -> tuple[str, Optional[DcinsideParseError]]:
        resolved = urlparse(urljoin(self.base_url, href))
        base = urlparse(self.base_url)
        if (
            resolved.scheme.casefold() != base.scheme.casefold()
            or resolved.netloc.casefold() != base.netloc.casefold()
        ):
            return "", DcinsideParseError(
                external_post_id,
                "invalid_post_link_origin",
                "Post link must use the same origin as the target board.",
            )

        query = parse_qs(resolved.query, keep_blank_values=True)
        board_values = query.get("id", [])
        post_id_values = query.get("no", [])
        if (
            self._post_board_id is None
            or len(board_values) != 1
            or board_values[0].strip() != self._post_board_id
        ):
            return "", DcinsideParseError(
                external_post_id,
                "invalid_post_link_board",
                "Post link board id does not match the target board.",
            )
        if len(post_id_values) != 1 or post_id_values[0].strip() != external_post_id:
            return "", DcinsideParseError(
                external_post_id,
                "invalid_post_link_id",
                "Post link no parameter does not exactly match the row data-no.",
            )
        return resolved.geturl(), None

    def _observe_comment_token(
        self, row: Dict[str, object], text: str, *, source: str
    ) -> None:
        match = COMMENT_BRACKET_PATTERN.fullmatch(text)
        if not match:
            row["comment_errors"].append(
                self._row_error(
                    row,
                    "invalid_comment_count",
                    f"{source} comment count is not a single bracketed integer: {text!r}.",
                )
            )
            return
        try:
            value = parse_dcinside_comment_count(match.group(1))
        except ValueError as exc:
            row["comment_errors"].append(
                self._row_error(row, "invalid_comment_count", str(exc))
            )
            return
        row["comment_observations"].append(value)

    def _finish_non_numeric_row(self, row: Dict[str, object]) -> None:
        data_type = str(row["data_type"])
        row_classes = set(row["row_class_tokens"])
        subject_label = " ".join(row["subject_text_parts"]).strip().casefold()
        normal_view_link_seen = bool(row["normal_view_link_seen"])
        interview_auxiliary = (
            bool(row["interview_signal_seen"])
            and str(row["external_post_id"]) == ""
            and data_type == ""
            and row_classes == {"ub-content"}
            and subject_label == "이슈"
            and int(row["title_link_count"]) == 1
        )
        advertisement_auxiliary = (
            str(row["external_post_id"]) == ""
            and data_type == ""
            and row_classes == {"ub-content"}
            and subject_label == "ad"
            and int(row["title_link_count"]) == 1
            and int(row["advertisement_icon_count"]) == 1
        )
        explicitly_auxiliary = (
            data_type == "icon_survey"
            or "survey" in row_classes
            or bool(row["survey_signal_seen"])
            or interview_auxiliary
            or advertisement_auxiliary
            or subject_label in SURVEY_SUBJECT_LABELS
        ) and not normal_view_link_seen
        if explicitly_auxiliary:
            return

        external_post_id = str(row["external_post_id"]) or "<missing>"
        self.diagnostics.errors.append(
            DcinsideParseError(
                external_post_id,
                "non_numeric_post_id",
                "Non-numeric row was not a recognized notice/survey auxiliary row.",
            )
        )

    def _reset_row_state(self) -> None:
        self.current_row = None
        self.current_cell = None
        self.capture_title = False
        self.capture_reply = False
        self._reply_capture_depth = 0
        self._reply_text_parts = []

    @staticmethod
    def _row_error(
        row: Dict[str, object], code: str, message: str
    ) -> DcinsideParseError:
        return DcinsideParseError(str(row.get("external_post_id") or "<missing>"), code, message)

    def _handle_navigation_starttag(self, tag: str, attrs: Dict[str, str]) -> bool:
        class_tokens = set(attrs.get("class", "").split())
        if tag == "div":
            if self._paging_depth > 0:
                self._paging_depth += 1
                return True
            if PAGING_CONTAINER_CLASS in class_tokens:
                self._paging_depth = 1
                self.navigation.paging_container_seen = True
                self.navigation.paging_container_count += 1
                return True
            return False

        if self._paging_depth <= 0:
            return False

        if tag in {"em", "a"}:
            if self._navigation_capture is not None:
                self._add_navigation_error(
                    "nested_navigation_capture",
                    "Pagination current/link elements were unexpectedly nested.",
                )
            else:
                self._navigation_capture = {
                    "tag": tag,
                    "href": attrs.get("href", "").strip(),
                    "class_tokens": class_tokens,
                    "text_parts": [],
                }
        return True

    def _handle_navigation_endtag(self, tag: str) -> bool:
        if self._paging_depth <= 0:
            return False

        capture = self._navigation_capture
        if capture is not None and tag == capture["tag"]:
            self._finish_navigation_capture(capture)
            self._navigation_capture = None

        if tag == "div":
            self._paging_depth -= 1
            if self._paging_depth == 0:
                self.navigation.paging_container_closed_count += 1
                if self._navigation_capture is not None:
                    self._add_navigation_error(
                        "unterminated_navigation_element",
                        "Pagination element was still open when its container ended.",
                    )
                    self._navigation_capture = None
                self._record_navigation_relationship_errors()
        return True

    def _handle_navigation_data(self, data: str) -> bool:
        if self._paging_depth <= 0:
            return False
        if self._navigation_capture is not None:
            cleaned = " ".join(data.split())
            if cleaned:
                self._navigation_capture["text_parts"].append(cleaned)
        return True

    def _finish_navigation_capture(self, capture: Dict[str, object]) -> None:
        tag = str(capture["tag"])
        text = " ".join(capture["text_parts"]).strip()
        if tag == "em":
            self._observe_current_page(text)
            return

        class_tokens = set(capture["class_tokens"])
        roles = set()
        if PAGE_NEXT_CLASS in class_tokens or text == PAGE_NEXT_TEXT:
            roles.add("next")
        if PAGE_END_CLASS in class_tokens or text == PAGE_END_TEXT:
            roles.add("last")
        if not roles:
            return
        if len(roles) != 1:
            self._add_navigation_error(
                "conflicting_navigation_role",
                f"Pagination link has conflicting next/end signals: {text!r}.",
            )
            return

        href = str(capture["href"])
        parsed_link = self._validated_navigation_link(href)
        if parsed_link is None:
            return
        board_id, page = parsed_link
        self.navigation.observed_board_ids.append(board_id)
        role = next(iter(roles))
        observations = (
            self.navigation.next_page_observations
            if role == "next"
            else self.navigation.last_page_observations
        )
        if observations:
            code = f"multiple_{role}_links"
            self._add_navigation_error(
                code,
                f"More than one validated {role} pagination link was found.",
            )
        observations.append(page)

    def _observe_current_page(self, text: str) -> None:
        if not ASCII_POST_ID_PATTERN.fullmatch(text) or int(text) <= 0:
            self._add_navigation_error(
                "invalid_current_page",
                f"Pagination current-page marker is not a positive integer: {text!r}.",
            )
            return
        page = int(text)
        if self.navigation.current_page_observations:
            self._add_navigation_error(
                "multiple_current_pages",
                "More than one pagination current-page marker was found.",
            )
        self.navigation.current_page_observations.append(page)
        requested_page = self.navigation.requested_page
        if requested_page is not None and page != requested_page:
            self._add_navigation_error(
                "requested_page_mismatch",
                f"Requested page {requested_page} was rendered as page {page}.",
            )

    def _validated_navigation_link(self, href: str) -> Optional[tuple[str, int]]:
        if not href:
            self._add_navigation_error(
                "missing_navigation_href",
                "Pagination next/end link has no href.",
            )
            return None

        base = urlparse(self.base_url)
        resolved = urlparse(urljoin(self.base_url, href))
        if (
            resolved.scheme.casefold() != base.scheme.casefold()
            or resolved.netloc.casefold() != base.netloc.casefold()
            or resolved.path.rstrip("/") != base.path.rstrip("/")
        ):
            self._add_navigation_error(
                "invalid_navigation_destination",
                f"Pagination link does not target the expected board-list endpoint: {href!r}.",
            )
            return None

        query = parse_qs(resolved.query, keep_blank_values=True)
        board_values = query.get("id", [])
        page_values = query.get("page", [])
        if len(board_values) != 1 or not board_values[0].strip():
            self._add_navigation_error(
                "invalid_navigation_board_id",
                f"Pagination href must contain exactly one non-empty board id: {href!r}.",
            )
            return None
        if len(page_values) != 1:
            self._add_navigation_error(
                "invalid_navigation_page",
                f"Pagination href must contain exactly one page value: {href!r}.",
            )
            return None

        board_id = board_values[0].strip()
        page_text = page_values[0].strip()
        if not ASCII_POST_ID_PATTERN.fullmatch(page_text) or int(page_text) <= 0:
            self._add_navigation_error(
                "invalid_navigation_page",
                f"Pagination href page is not a positive integer: {href!r}.",
            )
            return None
        expected_board_id = self.navigation.expected_board_id
        if expected_board_id is not None and board_id != expected_board_id:
            self._add_navigation_error(
                "navigation_board_mismatch",
                f"Pagination href board {board_id!r} does not match {expected_board_id!r}.",
            )
            return None
        return board_id, int(page_text)

    def _add_navigation_error(self, code: str, message: str) -> None:
        error = DcinsideNavigationError(code=code, message=message)
        if error not in self.navigation.errors:
            self.navigation.errors.append(error)

    def _record_navigation_relationship_errors(self) -> None:
        current_page = self.navigation.current_page
        next_page = self.navigation.next_page
        last_page = self.navigation.last_page
        if current_page is None:
            return
        if next_page is not None and next_page <= current_page:
            self._add_navigation_error(
                "next_page_not_after_current",
                f"Next page {next_page} must be after current page {current_page}.",
            )
        if last_page is not None and last_page < current_page:
            self._add_navigation_error(
                "last_page_before_current",
                f"Last page {last_page} cannot be before current page {current_page}.",
            )
        if next_page is not None and last_page is not None and next_page > last_page:
            self._add_navigation_error(
                "next_page_after_last",
                f"Next page {next_page} cannot be after last page {last_page}.",
            )

    def _build_post(
        self, row: Dict[str, object]
    ) -> tuple[Optional[DcinsidePost], Optional[DcinsideParseError]]:
        external_post_id = str(row["external_post_id"])
        subject = " ".join(row["subject_text_parts"]).strip()
        title = " ".join(row["title_parts"]).strip()
        created_at_raw = str(row["created_at_raw"]).strip()
        created_at_title = str(row["created_at_title"]).strip()

        subject_cell_count = int(row["subject_cell_count"])
        if subject_cell_count == 0:
            return None, self._row_error(
                row, "missing_subject_cell", "Candidate row has no subject cell."
            )
        if subject_cell_count != 1:
            return None, self._row_error(
                row,
                "multiple_subject_cells",
                "Candidate row must have exactly one subject cell.",
            )

        subject_inner_count = int(row["subject_inner_count"])
        if subject_inner_count > 1:
            return None, self._row_error(
                row,
                "multiple_subject_inner_labels",
                "Candidate row exposes more than one expanded subject label.",
            )
        if subject_inner_count == 1:
            expanded_subject = " ".join(row["subject_inner_text_parts"]).strip()
            if not expanded_subject:
                return None, self._row_error(
                    row,
                    "missing_subject_inner_text",
                    "Candidate row has an empty expanded subject label.",
                )
            subject = expanded_subject

        date_cell_count = int(row["date_cell_count"])
        if date_cell_count == 0:
            return None, self._row_error(
                row, "missing_date_cell", "Candidate row has no creation-date cell."
            )
        if date_cell_count != 1:
            return None, self._row_error(
                row,
                "multiple_date_cells",
                "Candidate row must have exactly one creation-date cell.",
            )

        required_values = (
            ("missing_title", "title", title),
            ("missing_datetime", "creation datetime", created_at_title or created_at_raw),
        )
        for code, label, value in required_values:
            if not value:
                return None, DcinsideParseError(
                    external_post_id=external_post_id,
                    code=code,
                    message=f"Candidate row is missing {label}.",
                )

        post_link_attempts = int(row["post_link_attempts"])
        if post_link_attempts == 0:
            return None, self._row_error(
                row, "missing_post_url", "Candidate row has no primary post view link."
            )
        if post_link_attempts != 1:
            return None, self._row_error(
                row,
                "multiple_post_links",
                "Candidate row must have exactly one primary post view link.",
            )
        post_link_errors = list(row["post_link_errors"])
        if post_link_errors:
            return None, post_link_errors[0]
        post_urls = list(row["post_urls"])
        if len(post_urls) != 1:
            return None, self._row_error(
                row, "invalid_post_link", "Candidate row post link could not be validated."
            )
        post_url = str(post_urls[0])

        recommend_cell_count = int(row["recommend_cell_count"])
        if recommend_cell_count == 0:
            return None, self._row_error(
                row, "missing_recommend_cell", "Candidate row has no recommendation cell."
            )
        if recommend_cell_count != 1:
            return None, self._row_error(
                row,
                "multiple_recommend_cells",
                "Candidate row must have exactly one recommendation cell.",
            )
        recommend_text = " ".join(row["recommend_text_parts"]).strip()
        try:
            upvotes = parse_strict_nonnegative_integer(recommend_text)
        except ValueError as exc:
            return None, self._row_error(row, "invalid_recommend_count", str(exc))

        comment_errors = list(row["comment_errors"])
        if comment_errors:
            return None, comment_errors[0]
        comment_observations = list(row["comment_observations"])
        if len(comment_observations) > 1:
            return None, self._row_error(
                row,
                "multiple_comment_counts",
                "Candidate row exposes more than one comment count.",
            )
        if bool(row["comment_markup_seen"]) and not comment_observations:
            return None, self._row_error(
                row,
                "missing_comment_count",
                "Reply-like markup was present without a bracketed comment count.",
            )
        comments = int(comment_observations[0]) if comment_observations else 0

        try:
            created_at = normalize_dcinside_datetime(created_at_title or created_at_raw, self.now)
        except (TypeError, ValueError) as exc:
            return None, DcinsideParseError(
                external_post_id=external_post_id,
                code="invalid_datetime",
                message=f"Could not parse creation datetime: {exc}",
            )

        return (
            DcinsidePost(
                external_post_id=external_post_id,
                subject=subject,
                title=title,
                post_url=post_url,
                created_at=created_at,
                created_at_raw=created_at_raw or created_at_title,
                upvotes=upvotes,
                comments=comments,
                qualifies_by=build_qualifies_by(
                    upvotes,
                    comments,
                    min_upvotes=self.min_upvotes,
                    min_comments=self.min_comments,
                ),
            ),
            None,
        )


def build_qualifies_by(
    upvotes: int,
    comments: int,
    min_upvotes: int = DEFAULT_MIN_UPVOTES,
    min_comments: int = DEFAULT_MIN_COMMENTS,
) -> str:
    if not meets_collection_threshold(
        upvotes,
        comments,
        min_upvotes=min_upvotes,
        min_comments=min_comments,
    ):
        return "none"

    reasons = []
    if upvotes >= min_upvotes:
        reasons.append("upvotes")
    if comments >= min_comments:
        reasons.append("comments")
    return "+".join(reasons) if reasons else "upvotes+comments"


def meets_collection_threshold(
    upvotes: int,
    comments: int,
    min_upvotes: int = DEFAULT_MIN_UPVOTES,
    min_comments: int = DEFAULT_MIN_COMMENTS,
) -> bool:
    """Return whether the combined engagement score reaches the target.

    ``min_upvotes`` and ``min_comments`` are the solo thresholds: reaching
    either one without help from the other metric is sufficient. Cross
    multiplication keeps mixed scores exact without floating-point math.
    """

    if min_upvotes <= 0 or min_comments <= 0:
        raise ValueError("Collection thresholds must be positive integers.")
    return (
        upvotes * min_comments + comments * min_upvotes
        >= min_upvotes * min_comments
    )


def is_qualifying_post(post: DcinsidePost, min_upvotes: int, min_comments: int) -> bool:
    return meets_collection_threshold(
        post.upvotes,
        post.comments,
        min_upvotes=min_upvotes,
        min_comments=min_comments,
    )


def normalize_dcinside_datetime(raw_value: str, now: Optional[datetime] = None) -> str:
    current = as_kst(now or datetime.now(KST))
    raw_value = raw_value.strip()

    full_datetime_match = FULL_DATETIME_PATTERN.fullmatch(raw_value)
    if full_datetime_match:
        year, month, day, hour, minute, second = map(int, full_datetime_match.groups())
        return datetime(year, month, day, hour, minute, second, tzinfo=KST).isoformat()

    time_match = TIME_PATTERN.fullmatch(raw_value)
    if time_match:
        hour, minute = map(int, time_match.groups())
        candidate = current.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate > current + timedelta(minutes=1):
            candidate -= timedelta(days=1)
        return candidate.isoformat()

    short_date_match = SHORT_DATE_PATTERN.fullmatch(raw_value)
    if short_date_match:
        year, month, day = map(int, short_date_match.groups())
        return datetime(2000 + year, month, day, 23, 59, 59, tzinfo=KST).isoformat()

    full_date_match = FULL_DATE_PATTERN.fullmatch(raw_value)
    if full_date_match:
        year, month, day = map(int, full_date_match.groups())
        return datetime(year, month, day, 23, 59, 59, tzinfo=KST).isoformat()

    raise ValueError(f"unsupported DCInside datetime value {raw_value!r}")


def as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def normalize_optional_page(value: Optional[int]) -> tuple[Optional[int], str]:
    if value is None:
        return None, ""
    if isinstance(value, bool):
        return None, "requested_page must be a positive integer"
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, "requested_page must be a positive integer"
    if parsed <= 0 or str(parsed) != str(value).strip():
        return None, "requested_page must be a lossless positive integer"
    return parsed, ""


def normalize_expected_board_id(
    base_url: str, expected_board_id: Optional[str]
) -> tuple[Optional[str], str]:
    values = parse_qs(urlparse(base_url).query, keep_blank_values=True).get("id", [])
    base_board_id = values[0].strip() if len(values) == 1 and values[0].strip() else None
    if expected_board_id is not None:
        normalized = str(expected_board_id).strip()
        if not normalized:
            return None, "expected_board_id cannot be empty"
        if base_board_id is not None and normalized != base_board_id:
            return normalized, "expected_board_id does not match the board id in base_url"
        return normalized, ""

    if base_board_id is not None:
        return base_board_id, ""
    return None, "base_url must contain exactly one board id when expected_board_id is omitted"


def board_id_from_url(url: str) -> Optional[str]:
    values = parse_qs(urlparse(url).query, keep_blank_values=True).get("id", [])
    if len(values) != 1 or not values[0].strip():
        return None
    return values[0].strip()


def _single_observation(values: List[int]) -> Optional[int]:
    return values[0] if len(values) == 1 else None


def navigation_page_relationships_are_valid(
    navigation: DcinsideNavigationDiagnostics,
) -> bool:
    current_page = navigation.current_page
    next_page = navigation.next_page
    last_page = navigation.last_page
    if current_page is None:
        return False
    if next_page is not None and next_page <= current_page:
        return False
    if last_page is not None and last_page < current_page:
        return False
    if next_page is not None and last_page is not None and next_page > last_page:
        return False
    return True


def are_post_ids_strictly_descending(post_ids: Iterable[str]) -> bool:
    numeric_ids = _canonical_post_ids(post_ids)
    if numeric_ids is None:
        return False
    previous: Optional[int] = None
    for current in numeric_ids:
        if previous is not None and previous <= current:
            return False
        previous = current
    return True


def are_post_ids_coverage_ordered(post_ids: Iterable[str]) -> bool:
    """Accept descending IDs plus disjoint swaps of consecutive ID pairs.

    DCInside can expose two concurrently committed neighboring posts as
    ``n-1, n``. Wider disorder remains unsafe because page boundaries and
    coverage search assume the canonical descending list order.
    """

    numeric_ids = _canonical_post_ids(post_ids)
    if numeric_ids is None or len(set(numeric_ids)) != len(numeric_ids):
        return False

    expected = sorted(numeric_ids, reverse=True)
    index = 0
    while index < len(numeric_ids):
        if numeric_ids[index] == expected[index]:
            index += 1
            continue
        if (
            index + 1 < len(numeric_ids)
            and numeric_ids[index] == expected[index + 1]
            and numeric_ids[index + 1] == expected[index]
            and expected[index] == expected[index + 1] + 1
        ):
            index += 2
            continue
        return False
    return True


def _canonical_post_ids(post_ids: Iterable[str]) -> Optional[List[int]]:
    numeric_ids: List[int] = []
    for post_id in post_ids:
        normalized = str(post_id).strip()
        if not ASCII_POST_ID_PATTERN.fullmatch(normalized):
            return None
        numeric_id = int(normalized)
        if numeric_id <= 0 or normalized != str(numeric_id):
            return None
        numeric_ids.append(numeric_id)
    return numeric_ids


def safe_int(value: str) -> int:
    return parse_strict_nonnegative_integer(value)


def parse_strict_nonnegative_integer(value: str) -> int:
    """Parse an unsigned count; conventional comma grouping is allowed."""

    normalized = str(value).strip()
    if PLAIN_COUNT_PATTERN.fullmatch(normalized):
        return int(normalized)
    if GROUPED_COUNT_PATTERN.fullmatch(normalized):
        return int(normalized.replace(",", ""))
    raise ValueError(
        f"count must be unsigned ASCII digits with optional 3-digit comma grouping: {value!r}"
    )


def parse_dcinside_comment_count(value: str) -> int:
    """Parse the list-page comment count, including DCInside's total/subcount form.

    DCInside can render a reply badge such as ``[8/1]`` while the post page
    reports eight total comments. The second value is auxiliary, so collection
    uses the first value while still validating both sides strictly.
    """

    normalized = str(value).strip()
    pair_match = COMMENT_COUNT_PAIR_PATTERN.fullmatch(normalized)
    if pair_match:
        parse_strict_nonnegative_integer(pair_match.group(2))
        return parse_strict_nonnegative_integer(pair_match.group(1))
    return parse_strict_nonnegative_integer(normalized)
