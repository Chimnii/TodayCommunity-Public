from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetBoard:
    key: str
    site_name: str
    board_name: str
    board_url: str
    list_url_template: str
    min_upvotes: int
    min_comments: int


TARGETS = {
    "dcinside-singularity": TargetBoard(
        key="dcinside-singularity",
        site_name="dcinside",
        board_name="특이점이 온다 마이너 갤러리",
        board_url="https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity",
        list_url_template="https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page={page}",
        min_upvotes=4,
        min_comments=20,
    ),
}


def get_target(target_key: str) -> TargetBoard:
    try:
        return TARGETS[target_key]
    except KeyError as exc:
        available = ", ".join(sorted(TARGETS))
        raise KeyError(f"Unknown target '{target_key}'. Available: {available}") from exc
