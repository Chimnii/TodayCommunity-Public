from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class SubjectCollectionRule:
    """An exact subject match with independent, optional count floors (AND)."""

    subject: str
    min_upvotes: int = 0
    min_comments: int = 0

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("A subject collection rule needs a nonempty subject")
        for value in (self.min_upvotes, self.min_comments):
            if type(value) is not int or value < 0:
                raise ValueError("Subject count floors must be nonnegative integers")


def matches_subject_rule(
    subject: str,
    upvotes: int,
    comments: int,
    rules: Sequence[SubjectCollectionRule],
) -> bool:
    return any(
        subject.strip() == rule.subject.strip()
        and upvotes >= rule.min_upvotes
        and comments >= rule.min_comments
        for rule in rules
    )
