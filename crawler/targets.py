from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ArchiveDefinition:
    key: str
    display_name: str
    description: str
    display_order: int
    is_public: bool = True


@dataclass(frozen=True)
class TargetBoard:
    # The first seven fields are kept in their original order so existing
    # callers that construct a TargetBoard positionally remain compatible.
    key: str
    site_name: str
    board_name: str
    board_url: str
    list_url_template: str
    min_upvotes: int
    min_comments: int
    archive_key: str = "dcinside-singularity"
    collector_kind: str = "dcinside-board"
    origin_key: str = "dcinside"
    policy: str = "weighted-engagement"
    collect_all: bool = False
    canonical_namespace: str = "dcinside:thesingularity"
    first_page_url: Optional[str] = None
    hot_lookback_minutes: float = 240.0
    hot_max_seconds: float = 180.0
    hot_max_pages: int = 30
    backfill_max_seconds: float = 600.0
    backfill_max_pages: int = 60
    request_interval_seconds: float = 10.0
    finalization_age_hours: float = 12.0
    block_cooldown_hours: float = 6.0

    @property
    def archive(self) -> ArchiveDefinition:
        return get_archive(self.archive_key)

    def page_url(self, page: int) -> str:
        normalized_page = max(1, int(page))
        if normalized_page == 1:
            return self.first_page_url or self.board_url
        return self.list_url_template.format(page=normalized_page)


ARCHIVES = {
    "dcinside-singularity": ArchiveDefinition(
        key="dcinside-singularity",
        display_name="특이점이 온다",
        description="디시인사이드 특이점이 온다 갤러리 인기글",
        display_order=10,
    ),
    "dcinside-agent-stack": ArchiveDefinition(
        key="dcinside-agent-stack",
        display_name="에이전트 스택",
        description="디시인사이드 에이전트 스택 갤러리 인기글",
        display_order=20,
    ),
    "fmkorea-munich": ArchiveDefinition(
        key="fmkorea-munich",
        display_name="뮌헨",
        description="에펨코리아의 뮌헨 관련 인기글",
        display_order=30,
    ),
}


TARGETS = {
    "dcinside-singularity": TargetBoard(
        key="dcinside-singularity",
        site_name="dcinside",
        board_name="특이점이 온다 마이너 갤러리",
        board_url="https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity",
        list_url_template=(
            "https://gall.dcinside.com/mgallery/board/lists/"
            "?id=thesingularity&page={page}"
        ),
        min_upvotes=4,
        min_comments=20,
        archive_key="dcinside-singularity",
        collector_kind="dcinside-board",
        origin_key="dcinside",
        canonical_namespace="dcinside:thesingularity",
        hot_max_seconds=180.0,
        backfill_max_seconds=600.0,
    ),
    "dcinside-agent-stack": TargetBoard(
        key="dcinside-agent-stack",
        site_name="dcinside",
        board_name="에이전트 스택(Agent Stack) 마이너 갤러리",
        board_url="https://gall.dcinside.com/mgallery/board/lists/?id=agent_stack",
        list_url_template=(
            "https://gall.dcinside.com/mgallery/board/lists/"
            "?id=agent_stack&page={page}"
        ),
        min_upvotes=10,
        min_comments=100,
        archive_key="dcinside-agent-stack",
        collector_kind="dcinside-board",
        origin_key="dcinside",
        canonical_namespace="dcinside:agent_stack",
        hot_max_seconds=240.0,
        backfill_max_seconds=480.0,
    ),
    "fmkorea-best-munich-search": TargetBoard(
        key="fmkorea-best-munich-search",
        site_name="fmkorea",
        board_name="포텐 터짐 '뮌헨' 검색",
        board_url=(
            "https://www.fmkorea.com/search.php?mid=best&"
            "search_keyword=%EB%AE%8C%ED%97%A8&search_target=title_content"
        ),
        list_url_template=(
            "https://www.fmkorea.com/index.php?mid=best&"
            "search_keyword=%EB%AE%8C%ED%97%A8&search_target=title_content&"
            "page={page}"
        ),
        min_upvotes=0,
        min_comments=0,
        archive_key="fmkorea-munich",
        collector_kind="fmkorea-search",
        origin_key="fmkorea",
        policy="collect-all",
        collect_all=True,
        canonical_namespace="fmkorea",
        first_page_url=(
            "https://www.fmkorea.com/search.php?mid=best&"
            "search_keyword=%EB%AE%8C%ED%97%A8&search_target=title_content"
        ),
        hot_lookback_minutes=1440.0,
        hot_max_seconds=180.0,
        hot_max_pages=20,
        backfill_max_seconds=360.0,
        backfill_max_pages=40,
        request_interval_seconds=15.0,
    ),
    "fmkorea-bayern-board": TargetBoard(
        key="fmkorea-bayern-board",
        site_name="fmkorea",
        board_name="해외축구 바이에른 게시판",
        board_url=(
            "https://www.fmkorea.com/index.php?mid=football_world&"
            "category=853073246"
        ),
        list_url_template=(
            "https://www.fmkorea.com/index.php?mid=football_world&"
            "category=853073246&page={page}"
        ),
        min_upvotes=15,
        min_comments=150,
        archive_key="fmkorea-munich",
        collector_kind="fmkorea-board",
        origin_key="fmkorea",
        canonical_namespace="fmkorea",
        hot_lookback_minutes=720.0,
        hot_max_seconds=180.0,
        hot_max_pages=20,
        backfill_max_seconds=360.0,
        backfill_max_pages=40,
        request_interval_seconds=15.0,
    ),
}


def get_archive(archive_key: str) -> ArchiveDefinition:
    try:
        return ARCHIVES[archive_key]
    except KeyError as exc:
        available = ", ".join(sorted(ARCHIVES))
        raise KeyError(
            f"Unknown archive '{archive_key}'. Available: {available}"
        ) from exc


def get_target(target_key: str) -> TargetBoard:
    try:
        return TARGETS[target_key]
    except KeyError as exc:
        available = ", ".join(sorted(TARGETS))
        raise KeyError(f"Unknown target '{target_key}'. Available: {available}") from exc


def iter_targets() -> Tuple[TargetBoard, ...]:
    return tuple(TARGETS.values())


def canonical_post_key(target: TargetBoard, external_post_id: str) -> str:
    normalized_id = str(external_post_id).strip()
    if not normalized_id:
        raise ValueError("external_post_id must not be empty")
    namespace = target.canonical_namespace.strip()
    if not namespace:
        raise ValueError(f"Target {target.key!r} has no canonical namespace")
    return f"{namespace}:{normalized_id}"
