from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from crawler.parsers.fmkorea import (
    FmkoreaBoardParser,
    FmkoreaPost,
    FmkoreaSearchParser,
    is_fmkorea_qualifying_post,
    normalize_fmkorea_datetime,
    parse_count,
)


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 7, 22, 0, 39, tzinfo=KST)
BASE = "https://www.fmkorea.com/index.php?mid=best"


def search_row(
    document_id: int = 123456,
    *,
    row_classes: str = (
        "li li_best2_pop0 li_best2_hotdeal0 li_best2_politics0"
    ),
    include_row_id: bool = False,
    href: str = "",
    auxiliary_id: int | None = None,
    vote_href: str | None = None,
    title: str = "뮌헨 새 소식",
    category: str = "축구",
    upvotes: str = "270",
    comments: str | None = "[263]",
    date: str = "4 시간 전",
    date_comment: str = "20:39",
    close: bool = True,
) -> str:
    post_href = href or f"/index.php?mid=best&document_srl={document_id}"
    vote_id = auxiliary_id if auxiliary_id is not None else document_id
    resolved_vote_href = (
        f"/index.php?document_srl={vote_id}" if vote_href is None else vote_href
    )
    comment_html = (
        f'<span class="comment_count">{comments}</span>'
        if comments is not None
        else ""
    )
    ending = "</li>" if close else ""
    row_id = f' data-document-srl="{document_id}"' if include_row_id else ""
    return (
        f'<li class="{row_classes}"{row_id}>'
        '<div class="li">'
        '<a class="pc_voted_count pc_voted_count_plus pc_voted_count_short" '
        f'href="{resolved_vote_href}"><span class="label">추천</span>'
        f'<span class="count">{upvotes}</span></a>'
        '<a class="thumb" href="/thumbnail"><img alt=""></a>'
        '<h3 class="title data-title-ellipsis">'
        f'<a class="hotdeal_var8" href="{post_href}">'
        f'<span class="ellipsis-target">{title}</span>{comment_html}</a></h3>'
        '<div class="meta"><span class="category">'
        f'<a>{category}</a></span></div>'
        '<div class="meta"><span class="regdate">'
        f'{date}<!--{date_comment}--></span><span class="author">작성자</span></div>'
        f"</div>{ending}"
    )


def board_row(
    document_id: int = 987654,
    *,
    href: str = "",
    title: str = "바이에른 경기 이야기",
    category: str = "바이에른",
    views: str = "9,999",
    upvotes: str = "14",
    comments: str | None = "[10]",
    date: str = "00:37",
    close: bool = True,
) -> str:
    post_href = href or f"/{document_id}"
    comment_html = (
        f'<a class="replyNum">{comments}</a>' if comments is not None else ""
    )
    ending = "</tr>" if close else ""
    return (
        f'<tr data-document-srl="{document_id}">'
        f'<td class="cate">{category}</td>'
        '<td class="title">'
        f'<a class="hx" href="{post_href}">{title}</a>{comment_html}'
        "</td>"
        f'<td class="time">{date}</td>'
        f'<td class="m_no">{views}</td>'
        f'<td class="m_no m_no_voted">{upvotes}</td>'
        f"{ending}"
    )


class FmkoreaSearchParserTests(unittest.TestCase):
    def parse(self, html: str) -> FmkoreaSearchParser:
        parser = FmkoreaSearchParser(
            base_url=BASE,
            now=NOW,
            min_upvotes=0,
            min_comments=0,
        )
        parser.feed(f"<ul>{html}</ul>")
        parser.close()
        return parser

    def test_parses_observed_search_shape_and_relative_comment_time(self) -> None:
        parser = self.parse(search_row())

        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertEqual(len(parser.posts), 1)
        post = parser.posts[0]
        self.assertEqual(post.external_post_id, "123456")
        self.assertEqual(post.title, "뮌헨 새 소식")
        self.assertEqual(post.subject, "축구")
        self.assertEqual(post.upvotes, 270)
        self.assertEqual(post.comments, 263)
        self.assertEqual(post.created_at, "2026-07-21T20:39:00+09:00")
        self.assertEqual(post.qualifies_by, "keyword")

    def test_keeps_support_for_legacy_search_row_marker(self) -> None:
        parser = self.parse(
            search_row(
                row_classes="li_best2 clear",
                include_row_id=True,
            )
        )

        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertEqual(parser.posts[0].external_post_id, "123456")

    def test_incomplete_current_row_signature_fails_closed(self) -> None:
        parser = self.parse(
            search_row(
                row_classes="li li_best2_pop0 li_best2_hotdeal0",
            )
        )

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertTrue(parser.diagnostics.row_container_seen)
        self.assertEqual(parser.diagnostics.candidate_rows, 1)
        self.assertIn(
            "invalid_search_row_signature",
            [error.code for error in parser.diagnostics.errors],
        )

    def test_mixed_valid_and_partial_signatures_fail_the_whole_page(self) -> None:
        parser = self.parse(
            search_row(document_id=123456)
            + search_row(
                document_id=123457,
                row_classes="li li_best2_pop0 li_best2_hotdeal0",
            )
        )

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertEqual(parser.diagnostics.candidate_rows, 2)
        self.assertIn(
            "invalid_search_row_signature",
            [error.code for error in parser.diagnostics.errors],
        )

    def test_current_signature_rejects_unknown_extra_classes(self) -> None:
        parser = self.parse(
            search_row(
                row_classes=(
                    "li li_best2_pop0 li_best2_hotdeal0 "
                    "li_best2_politics0 unexpected"
                ),
            )
        )

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertIn(
            "invalid_search_row_signature",
            [error.code for error in parser.diagnostics.errors],
        )

    def test_collects_zero_comment_row_and_grouped_count(self) -> None:
        parser = self.parse(search_row(upvotes="1,234", comments=None))

        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertEqual(parser.posts[0].upvotes, 1234)
        self.assertEqual(parser.posts[0].comments, 0)

    def test_rejects_row_and_auxiliary_document_id_mismatch(self) -> None:
        parser = self.parse(search_row(auxiliary_id=999999))

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertEqual(parser.diagnostics.parsed_rows, 0)
        self.assertIn(
            "document_id_mismatch",
            {error.code for error in parser.diagnostics.errors},
        )

    def test_rejects_invalid_auxiliary_vote_links(self) -> None:
        for vote_href in (
            "",
            "/index.php?document_srl=not-a-number",
            "https://example.com/?document_srl=123456",
        ):
            with self.subTest(vote_href=vote_href):
                parser = self.parse(search_row(vote_href=vote_href))

                self.assertFalse(parser.diagnostics.is_collection_safe)
                self.assertEqual(parser.diagnostics.parsed_rows, 0)
                self.assertIn(
                    "invalid_auxiliary_link",
                    {error.code for error in parser.diagnostics.errors},
                )

    def test_rejects_off_origin_post_link(self) -> None:
        parser = self.parse(
            search_row(href="https://example.com/?document_srl=123456")
        )

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertIn(
            "invalid_post_link",
            {error.code for error in parser.diagnostics.errors},
        )

    def test_rejects_truncated_candidate_row(self) -> None:
        parser = self.parse(search_row(close=False))

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertIn(
            "unterminated_candidate_row",
            {error.code for error in parser.diagnostics.errors},
        )

    def test_rejects_duplicate_canonical_id(self) -> None:
        parser = self.parse(search_row() + search_row())

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertIn(
            "duplicate_document_id",
            {error.code for error in parser.diagnostics.errors},
        )

    def test_rejects_malformed_count(self) -> None:
        parser = self.parse(search_row(upvotes="1.2천"))

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertIn(
            "invalid_candidate_metric",
            {error.code for error in parser.diagnostics.errors},
        )

    def test_relative_time_requires_stable_hidden_clock_evidence(self) -> None:
        parser = self.parse(search_row(date_comment=""))

        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertIn(
            "invalid_candidate_metric",
            {error.code for error in parser.diagnostics.errors},
        )

    def test_relative_time_crosses_midnight_using_hidden_clock(self) -> None:
        parser = FmkoreaSearchParser(
            base_url=BASE,
            now=datetime(2026, 7, 22, 0, 5, tzinfo=KST),
            min_upvotes=0,
            min_comments=0,
        )
        parser.feed(
            "<ul>"
            + search_row(date="1 시간 전", date_comment="23:05")
            + "</ul>"
        )
        parser.close()

        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertEqual(parser.posts[0].created_at, "2026-07-21T23:05:00+09:00")

    def test_navigation_rejects_rendered_page_mismatch(self) -> None:
        parser = FmkoreaSearchParser(
            base_url=BASE,
            now=NOW,
            min_upvotes=0,
            min_comments=0,
            requested_page=2,
        )
        parser.feed(
            "<ul>"
            + search_row()
            + "</ul>"
            '<div class="pagination"><strong>1</strong>'
            '<a href="/index.php?mid=best&amp;page=2">2</a></div>'
        )
        parser.close()

        self.assertFalse(parser.navigation.is_valid)
        self.assertEqual(parser.navigation.current_page, 1)


class FmkoreaBoardParserTests(unittest.TestCase):
    def parse(self, html: str) -> FmkoreaBoardParser:
        parser = FmkoreaBoardParser(
            base_url=(
                "https://www.fmkorea.com/index.php?mid=football_world&"
                "category=853073246"
            ),
            now=NOW,
            min_upvotes=15,
            min_comments=150,
        )
        parser.feed(f"<table><tbody>{html}</tbody></table>")
        parser.close()
        return parser

    def test_parses_board_shape_without_confusing_views_for_upvotes(self) -> None:
        parser = self.parse(board_row())

        self.assertTrue(parser.diagnostics.is_collection_safe)
        post = parser.posts[0]
        self.assertEqual(post.upvotes, 14)
        self.assertEqual(post.comments, 10)
        self.assertEqual(post.created_at, "2026-07-22T00:37:00+09:00")
        self.assertEqual(post.qualifies_by, "upvotes+comments")

    def test_parses_current_anonymous_board_shape_without_row_id(self) -> None:
        parser = self.parse(
            '<tr><td class="cate"><span><a>바이에른</a></span></td>'
            '<td class="title hotdeal_var8">'
            '<a class="hx" href="/index.php?mid=football_world&amp;category=853073246&amp;document_srl=987654">'
            '현재 게시물</a></td>'
            '<td class="author"><span>작성자</span></td>'
            '<td class="time">00:37</td>'
            '<td class="m_no">9,999</td>'
            '<td class="m_no m_no_voted">14</td></tr>'
        )

        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertEqual(parser.posts[0].external_post_id, "987654")
        self.assertEqual(parser.posts[0].comments, 0)
        self.assertEqual(parser.posts[0].upvotes, 14)

    def test_board_weighted_threshold_is_exact(self) -> None:
        parser = self.parse(board_row(upvotes="14", comments="[10]"))
        post = parser.posts[0]

        self.assertTrue(
            is_fmkorea_qualifying_post(
                post,
                collect_all=False,
                min_upvotes=15,
                min_comments=150,
            )
        )
        below = FmkoreaPost(
            **{
                **post.__dict__,
                "comments": 9,
                "qualifies_by": "none",
            }
        )
        self.assertFalse(
            is_fmkorea_qualifying_post(
                below,
                collect_all=False,
                min_upvotes=15,
                min_comments=150,
            )
        )

    def test_ignores_notice_and_header_rows(self) -> None:
        html = (
            "<tr><th>제목</th></tr>"
            '<tr class="notice"><td class="title">'
            '<a class="hx" href="/111">공지</a></td></tr>'
            + board_row()
        )
        parser = self.parse(html)

        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertEqual([post.external_post_id for post in parser.posts], ["987654"])


class FmkoreaValueParserTests(unittest.TestCase):
    def test_date_variants_are_normalized_in_kst(self) -> None:
        self.assertEqual(
            normalize_fmkorea_datetime("2026.07.20", now=NOW),
            "2026-07-20T23:59:59+09:00",
        )
        self.assertEqual(
            normalize_fmkorea_datetime("23:59", now=NOW),
            "2026-07-21T23:59:00+09:00",
        )
        self.assertEqual(
            normalize_fmkorea_datetime("30 분 전", comment_value="00:09", now=NOW),
            "2026-07-22T00:09:00+09:00",
        )

    def test_count_parser_rejects_loose_numeric_text(self) -> None:
        self.assertEqual(parse_count("1,234"), 1234)
        self.assertEqual(parse_count("-12", allow_negative=True), -12)
        with self.assertRaises(ValueError):
            parse_count("-12")
        with self.assertRaises(ValueError):
            parse_count("추천 12")


if __name__ == "__main__":
    unittest.main()
