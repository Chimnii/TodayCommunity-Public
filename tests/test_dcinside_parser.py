from __future__ import annotations

import unittest
from datetime import datetime

from crawler.parsers.dcinside import (
    KST,
    DcinsideListParser,
    are_post_ids_coverage_ordered,
    are_post_ids_strictly_descending,
    build_qualifies_by,
    is_qualifying_post,
    meets_collection_threshold,
    normalize_dcinside_datetime,
    parse_dcinside_comment_count,
    parse_strict_nonnegative_integer,
)


BASE_URL = "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity"
FIXED_NOW = datetime(2026, 7, 16, 3, 0, 0, tzinfo=KST)


def regular_row(
    post_id: str,
    *,
    subject: str = "일반",
    title: str = "테스트 글",
    date_text: str = "02:30",
    date_title: str = "2026-07-16 02:30:45",
    upvotes: str = "4",
    comments: int = 0,
    post_href: str | None = None,
    reply_markup: str | None = None,
    recommend_markup: str | None = None,
    subject_markup: str | None = None,
) -> str:
    title_attribute = f' title="{date_title}"' if date_title else ""
    href = post_href or (
        f"/mgallery/board/view/?id=thesingularity&amp;no={post_id}&amp;page=1"
    )
    reply = (
        reply_markup
        if reply_markup is not None
        else (f'<span class="reply_num">[{comments}]</span>' if comments else "")
    )
    recommend = (
        recommend_markup
        if recommend_markup is not None
        else f'<td class="gall_recommend">{upvotes}</td>'
    )
    subject_cell = (
        subject_markup
        if subject_markup is not None
        else f'<td class="gall_subject">{subject}</td>'
    )
    return f"""
    <tr class="ub-content us-post" data-no="{post_id}" data-type="icon_txt">
      <td class="gall_num">{post_id}</td>
      {subject_cell}
      <td class="gall_tit ub-word">
        <a href="{href}">{title}</a>
        {reply}
      </td>
      <td class="gall_date"{title_attribute}>{date_text}</td>
      {recommend}
    </tr>
    """


NOTICE_ROW = """
<tr class="ub-content" data-no="1295629" data-type="icon_notice">
  <td class="gall_tit"><a href="/mgallery/board/view/?id=thesingularity&amp;no=1295629">공지</a></td>
  <td class="gall_date" title="2026-07-07 03:02:24">26.07.07</td>
  <td class="gall_recommend">14</td>
</tr>
"""


SURVEY_ROW = """
<tr class="ub-content" data-no="" data-type="">
  <td class="gall_tit"><a href="/survey/">설문</a></td>
  <td class="gall_date">26/07/13</td>
</tr>
"""


LIVE_SURVEY_ROW = """
<tr class="ub-content " data-no="" data-type="">
  <td class="gall_num">-</td>
  <td class="gall_subject">\uc124\ubb38</td>
  <td class="gall_tit ub-word">
    <a class="font_blue3a7" href="javascript:;">survey title</a>
  </td>
  <td class="gall_date">26/07/13</td>
</tr>
"""


def pagination(
    *,
    current: int = 1,
    next_page: str = "16",
    last_page: str = "23685",
    board_id: str = "thesingularity",
    use_classes: bool = True,
) -> str:
    next_class = ' class="sp_pagingicon page_next"' if use_classes else ""
    end_class = ' class="sp_pagingicon page_end"' if use_classes else ""
    return f"""
    <div class="bottom_paging_box">
      <em>{current}</em>
      <a href="/mgallery/board/lists/?id={board_id}&amp;page=2">2</a>
      <a{next_class} href="/mgallery/board/lists/?id={board_id}&amp;page={next_page}">\ub2e4\uc74c</a>
      <a{end_class} href="/mgallery/board/lists/?id={board_id}&amp;page={last_page}">\ub05d</a>
    </div>
    """


class DcinsideDatetimeTests(unittest.TestCase):
    def test_full_title_datetime_is_kst_aware(self) -> None:
        self.assertEqual(
            normalize_dcinside_datetime("2026-07-15 23:58:59", FIXED_NOW),
            "2026-07-15T23:58:59+09:00",
        )

    def test_date_only_fallback_uses_end_of_day(self) -> None:
        self.assertEqual(
            normalize_dcinside_datetime("26.07.15", FIXED_NOW),
            "2026-07-15T23:59:59+09:00",
        )
        self.assertEqual(
            normalize_dcinside_datetime("26/07/15", FIXED_NOW),
            "2026-07-15T23:59:59+09:00",
        )

    def test_time_only_uses_fixed_now_and_handles_midnight_rollover(self) -> None:
        midnight = datetime(2026, 7, 16, 0, 0, 0, tzinfo=KST)
        self.assertEqual(
            normalize_dcinside_datetime("23:59", midnight),
            "2026-07-15T23:59:00+09:00",
        )

    def test_unsupported_datetime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_dcinside_datetime("yesterday", FIXED_NOW)


class DcinsideListParserTests(unittest.TestCase):
    def test_subject_is_parsed_separately_from_the_title(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(regular_row("105", subject="  AI 소식  ", title="새 모델 발표"))

        self.assertEqual(parser.posts[0].subject, "AI 소식")
        self.assertEqual(parser.posts[0].title, "새 모델 발표")

    def test_expanded_subject_label_is_preferred_over_visible_abbreviation(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(
            regular_row(
                "105",
                subject_markup=(
                    '<td class="gall_subject">☕작업'
                    '<p class="subject_inner" style="display:none">'
                    "☕작업잡담"
                    "</p></td>"
                ),
            )
        )

        self.assertEqual(parser.posts[0].subject, "☕작업잡담")
        self.assertTrue(parser.diagnostics.is_coverage_safe)

    def test_expanded_subject_label_must_be_unique_and_nonempty(self) -> None:
        duplicate = regular_row(
            "105",
            subject_markup=(
                '<td class="gall_subject">☕작업'
                '<p class="subject_inner">☕작업잡담</p>'
                '<p class="subject_inner">다른 말머리</p>'
                "</td>"
            ),
        )
        empty = regular_row(
            "105",
            subject_markup=(
                '<td class="gall_subject">☕작업'
                '<p class="subject_inner"></p>'
                "</td>"
            ),
        )

        for html, expected_code in (
            (duplicate, "multiple_subject_inner_labels"),
            (empty, "missing_subject_inner_text"),
        ):
            with self.subTest(expected_code=expected_code):
                parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
                parser.feed(html)
                self.assertEqual(parser.posts, [])
                self.assertIn(
                    expected_code,
                    [error.code for error in parser.diagnostics.errors],
                )
                self.assertFalse(parser.diagnostics.is_complete)

    def test_non_expanded_subject_keeps_nested_visible_text(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(
            regular_row(
                "105",
                subject_markup='<td class="gall_subject"><b>일반</b></td>',
            )
        )

        self.assertEqual(parser.posts[0].subject, "일반")

    def test_title_attribute_is_preferred_over_visible_datetime(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(
            regular_row(
                "105",
                date_text="26.07.15",
                date_title="2026-07-15 23:58:59",
            )
        )

        self.assertEqual(parser.posts[0].created_at, "2026-07-15T23:58:59+09:00")
        self.assertEqual(parser.posts[0].created_at_raw, "26.07.15")

    def test_diagnostics_exclude_notice_and_survey_rows(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(
            SURVEY_ROW
            + NOTICE_ROW
            + regular_row("105", comments=16)
            + regular_row("103", upvotes="0")
        )

        diagnostics = parser.diagnostics
        self.assertEqual(diagnostics.total_content_rows, 4)
        self.assertEqual(diagnostics.notice_rows, 1)
        self.assertEqual(diagnostics.non_numeric_rows, 1)
        self.assertEqual(diagnostics.candidate_rows, 2)
        self.assertEqual(diagnostics.parsed_rows, 2)
        self.assertEqual(diagnostics.failed_rows, 0)
        self.assertEqual(diagnostics.candidate_post_ids, ["105", "103"])
        self.assertTrue(diagnostics.is_complete)
        self.assertTrue(diagnostics.ids_strictly_descending)
        self.assertTrue(diagnostics.is_coverage_safe)
        self.assertEqual([post.external_post_id for post in parser.posts], ["105", "103"])

    def test_malformed_numeric_candidate_is_reported(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(regular_row("105", date_text="", date_title=""))

        diagnostics = parser.diagnostics
        self.assertEqual(diagnostics.candidate_rows, 1)
        self.assertEqual(diagnostics.parsed_rows, 0)
        self.assertEqual(diagnostics.failed_rows, 1)
        self.assertEqual(diagnostics.errors[0].external_post_id, "105")
        self.assertEqual(diagnostics.errors[0].code, "missing_datetime")
        self.assertFalse(diagnostics.is_complete)
        self.assertFalse(diagnostics.is_coverage_safe)

    def test_invalid_datetime_is_diagnostic_instead_of_parser_failure(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(regular_row("105", date_title="not-a-date"))

        self.assertEqual(parser.posts, [])
        self.assertEqual(parser.diagnostics.errors[0].code, "invalid_datetime")

    def test_out_of_order_ids_are_not_coverage_safe(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(
            regular_row("1324213")
            + regular_row("1324189")
            + regular_row("1324212")
        )

        self.assertTrue(parser.diagnostics.is_complete)
        self.assertTrue(parser.diagnostics.is_collection_safe)
        self.assertFalse(parser.diagnostics.ids_strictly_descending)
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_duplicate_ids_are_not_safe_even_for_collection(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(regular_row("105") + regular_row("105"))

        self.assertTrue(parser.diagnostics.is_complete)
        self.assertFalse(parser.diagnostics.has_unique_canonical_ids)
        self.assertFalse(parser.diagnostics.is_collection_safe)
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_empty_or_non_numeric_page_is_not_coverage_safe(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(SURVEY_ROW + NOTICE_ROW)

        self.assertTrue(parser.diagnostics.is_complete)
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_parser_thresholds_are_configurable(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            now=FIXED_NOW,
            min_upvotes=10,
            min_comments=20,
        )
        parser.feed(regular_row("105", upvotes="5", comments=10))

        post = parser.posts[0]
        self.assertEqual(post.qualifies_by, "upvotes+comments")
        self.assertTrue(is_qualifying_post(post, min_upvotes=10, min_comments=20))
        self.assertEqual(build_qualifies_by(5, 10), "upvotes")
        self.assertEqual(build_qualifies_by(4, 10, 10, 20), "none")

    def test_combined_collection_threshold_boundaries(self) -> None:
        cases = (
            (4, 0, True, "upvotes"),
            (3, 4, False, "none"),
            (3, 5, True, "upvotes+comments"),
            (2, 9, False, "none"),
            (2, 10, True, "upvotes+comments"),
            (1, 14, False, "none"),
            (1, 15, True, "upvotes+comments"),
            (0, 19, False, "none"),
            (0, 20, True, "comments"),
        )

        for upvotes, comments, expected, reason in cases:
            with self.subTest(upvotes=upvotes, comments=comments):
                self.assertEqual(
                    meets_collection_threshold(upvotes, comments),
                    expected,
                )
                self.assertEqual(build_qualifies_by(upvotes, comments), reason)

    def test_collection_thresholds_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            meets_collection_threshold(4, 0, min_upvotes=0, min_comments=20)

    def test_post_id_order_helper_rejects_duplicates_and_non_numeric_ids(self) -> None:
        self.assertTrue(are_post_ids_strictly_descending(["105", "103", "99"]))
        self.assertFalse(are_post_ids_strictly_descending(["105", "105"]))
        self.assertFalse(are_post_ids_strictly_descending(["105", "survey"]))

    def test_coverage_order_allows_only_disjoint_consecutive_swaps(self) -> None:
        self.assertTrue(
            are_post_ids_coverage_ordered(["4309", "4307", "4308", "4306"])
        )
        self.assertTrue(
            are_post_ids_coverage_ordered(
                ["109", "107", "108", "105", "106", "104"]
            )
        )
        for unsafe in (
            ["105", "105"],
            ["105", "0104"],
            ["105", "0"],
            ["105", "survey"],
            ["105", "102", "104", "103"],
            ["105", "103", "102", "104"],
        ):
            with self.subTest(unsafe=unsafe):
                self.assertFalse(are_post_ids_coverage_ordered(unsafe))


class DcinsideRowIntegrityTests(unittest.TestCase):
    def parse_row(self, html: str) -> DcinsideListParser:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW)
        parser.feed(html)
        return parser

    def assert_row_error(self, html: str, expected_code: str) -> DcinsideListParser:
        parser = self.parse_row(html)
        self.assertEqual(parser.posts, [])
        self.assertEqual(parser.diagnostics.candidate_rows, 1)
        self.assertEqual(parser.diagnostics.parsed_rows, 0)
        self.assertIn(
            expected_code,
            [error.code for error in parser.diagnostics.errors],
        )
        self.assertFalse(parser.diagnostics.is_complete)
        self.assertFalse(parser.diagnostics.is_coverage_safe)
        return parser

    def test_creation_date_cell_must_exist_exactly_once(self) -> None:
        missing = regular_row("105").replace(
            'class="gall_date"',
            'class="other"',
            1,
        )
        duplicate = regular_row("105").replace(
            '<td class="gall_recommend">',
            '<td class="gall_date" title="2026-07-16 02:30:45">02:30</td>'
            '<td class="gall_recommend">',
            1,
        )

        self.assert_row_error(missing, "missing_date_cell")
        self.assert_row_error(duplicate, "multiple_date_cells")

    def test_subject_cell_must_exist_exactly_once_but_may_be_empty(self) -> None:
        missing = regular_row("105", subject_markup="")
        duplicate = regular_row(
            "105",
            subject_markup=(
                '<td class="gall_subject">일반</td>'
                '<td class="gall_subject">AI 소식</td>'
            ),
        )

        self.assert_row_error(missing, "missing_subject_cell")
        self.assert_row_error(duplicate, "multiple_subject_cells")

        parser = self.parse_row(regular_row("105", subject=""))
        self.assertEqual(parser.posts[0].subject, "")
        self.assertTrue(parser.diagnostics.is_coverage_safe)

    def test_recommendation_cell_must_exist_exactly_once(self) -> None:
        fixtures = (
            ("missing_recommend_cell", ""),
            (
                "multiple_recommend_cells",
                '<td class="gall_recommend">4</td>'
                '<td class="gall_recommend">5</td>',
            ),
        )

        for expected_code, recommend_markup in fixtures:
            with self.subTest(expected_code=expected_code):
                self.assert_row_error(
                    regular_row("105", recommend_markup=recommend_markup),
                    expected_code,
                )

    def test_recommendation_count_is_strict_and_does_not_strip_signs(self) -> None:
        for malformed in ("-4", "+4", "1,23", "4 votes", ""):
            with self.subTest(malformed=malformed):
                self.assert_row_error(
                    regular_row("105", upvotes=malformed),
                    "invalid_recommend_count",
                )

        self.assertEqual(parse_strict_nonnegative_integer("1,234"), 1234)
        with self.assertRaises(ValueError):
            parse_strict_nonnegative_integer("-4")

        parser = self.parse_row(regular_row("105", upvotes="1,234"))
        self.assertEqual(parser.posts[0].upvotes, 1234)

    def test_changed_reply_class_still_uses_bracketed_title_cell_count(self) -> None:
        parser = self.parse_row(
            regular_row(
                "105",
                reply_markup='<span class="reply_count_changed">[16]</span>',
            )
        )

        self.assertEqual(parser.posts[0].comments, 16)
        self.assertTrue(parser.diagnostics.is_coverage_safe)

    def test_comment_total_with_auxiliary_subcount_uses_the_total(self) -> None:
        parser = self.parse_row(
            regular_row(
                "105",
                reply_markup='<span class="reply_num">[8/1]</span>',
            )
        )

        self.assertEqual(parser.posts[0].comments, 8)
        self.assertTrue(parser.diagnostics.is_coverage_safe)
        self.assertEqual(parse_dcinside_comment_count("1,234/5"), 1234)

        for malformed in ("8/", "/1", "8/-1", "8/1/0", "1,23/1"):
            with self.subTest(malformed=malformed):
                self.assert_row_error(
                    regular_row(
                        "105",
                        reply_markup=f'<span class="reply_num">[{malformed}]</span>',
                    ),
                    "invalid_comment_count",
                )

    def test_normal_zero_comment_row_is_distinct_from_broken_reply_markup(self) -> None:
        zero_parser = self.parse_row(regular_row("105"))
        self.assertEqual(zero_parser.posts[0].comments, 0)
        self.assertTrue(zero_parser.diagnostics.is_coverage_safe)

        self.assert_row_error(
            regular_row(
                "105",
                reply_markup='<span class="reply_count_changed">16</span>',
            ),
            "missing_comment_count",
        )

    def test_brackets_inside_primary_title_are_not_mistaken_for_comments(self) -> None:
        parser = self.parse_row(regular_row("105", title="[15] title"))

        self.assertEqual(parser.posts[0].title, "[15] title")
        self.assertEqual(parser.posts[0].comments, 0)

    def test_comment_count_rejects_malformed_and_duplicate_observations(self) -> None:
        malformed_fixtures = (
            '<span class="reply_num">[-4]</span>',
            '<span class="reply_num">[1,23]</span>',
            '<span class="reply_num">comments 16</span>',
        )
        for reply_markup in malformed_fixtures:
            with self.subTest(reply_markup=reply_markup):
                self.assert_row_error(
                    regular_row("105", reply_markup=reply_markup),
                    "invalid_comment_count",
                )

        self.assert_row_error(
            regular_row(
                "105",
                reply_markup=(
                    '<span class="reply_num">[16]</span>'
                    '<span class="reply_count_changed">[16]</span>'
                ),
            ),
            "multiple_comment_counts",
        )

    def test_unrecognized_non_numeric_post_row_blocks_coverage(self) -> None:
        suspicious_row = """
        <tr class="ub-content us-post" data-no="post-105" data-type="icon_txt">
          <td class="gall_tit">
            <a href="/mgallery/board/view/?id=thesingularity&amp;no=105">title</a>
          </td>
          <td class="gall_date">02:30</td>
          <td class="gall_recommend">4</td>
        </tr>
        """
        parser = self.parse_row(suspicious_row + regular_row("104"))

        self.assertEqual(parser.diagnostics.non_numeric_rows, 1)
        self.assertIn(
            "non_numeric_post_id",
            [error.code for error in parser.diagnostics.errors],
        )
        self.assertFalse(parser.diagnostics.is_complete)
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_explicit_survey_auxiliary_row_remains_excluded(self) -> None:
        parser = self.parse_row(SURVEY_ROW + regular_row("105"))

        self.assertEqual(parser.diagnostics.non_numeric_rows, 1)
        self.assertEqual(parser.diagnostics.errors, [])
        self.assertTrue(parser.diagnostics.is_coverage_safe)

    def test_structured_survey_subject_row_remains_excluded(self) -> None:
        parser = self.parse_row(LIVE_SURVEY_ROW + regular_row("105"))

        self.assertEqual(parser.diagnostics.non_numeric_rows, 1)
        self.assertEqual(parser.diagnostics.errors, [])
        self.assertTrue(parser.diagnostics.is_coverage_safe)

    def test_numeric_post_with_survey_text_subject_is_not_excluded(self) -> None:
        parser = self.parse_row(regular_row("105", subject="설문"))

        self.assertEqual(parser.posts[0].subject, "설문")
        self.assertTrue(parser.diagnostics.is_coverage_safe)

    def test_survey_subject_does_not_override_a_normal_post_link(self) -> None:
        suspicious_row = """
        <tr class="ub-content" data-no="" data-type="">
          <td class="gall_subject">\uc124\ubb38</td>
          <td class="gall_tit">
            <a href="/mgallery/board/view/?id=thesingularity&amp;no=105">title</a>
          </td>
        </tr>
        """
        parser = self.parse_row(suspicious_row + regular_row("104"))

        self.assertIn(
            "non_numeric_post_id",
            [error.code for error in parser.diagnostics.errors],
        )
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_approximate_survey_subject_is_not_excluded(self) -> None:
        suspicious_row = """
        <tr class="ub-content" data-no="" data-type="">
          <td class="gall_subject">\uc124\ubb38\uc870\uc0ac</td>
          <td class="gall_tit"><a href="javascript:;">title</a></td>
        </tr>
        """
        parser = self.parse_row(suspicious_row + regular_row("105"))

        self.assertIn(
            "non_numeric_post_id",
            [error.code for error in parser.diagnostics.errors],
        )
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_post_link_must_match_origin_board_and_row_id(self) -> None:
        fixtures = (
            (
                "invalid_post_link_origin",
                "https://example.com/mgallery/board/view/?id=thesingularity&amp;no=105",
            ),
            (
                "invalid_post_link_board",
                "/mgallery/board/view/?id=other&amp;no=105",
            ),
            (
                "invalid_post_link_id",
                "/mgallery/board/view/?id=thesingularity&amp;no=104",
            ),
        )

        for expected_code, post_href in fixtures:
            with self.subTest(expected_code=expected_code):
                self.assert_row_error(
                    regular_row("105", post_href=post_href),
                    expected_code,
                )

    def test_candidate_must_have_only_one_primary_post_link(self) -> None:
        html = regular_row(
            "105",
            reply_markup=(
                '<a href="/mgallery/board/view/?id=thesingularity&amp;no=105">'
                "duplicate"
                "</a>"
            ),
        )
        self.assert_row_error(html, "multiple_post_links")


class DcinsideNavigationTests(unittest.TestCase):
    def test_extracts_current_next_and_last_pages_from_validated_links(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            now=FIXED_NOW,
            requested_page=1,
            expected_board_id="thesingularity",
        )
        parser.feed(pagination())

        navigation = parser.navigation
        self.assertTrue(navigation.is_valid)
        self.assertEqual(navigation.current_page, 1)
        self.assertEqual(navigation.next_page, 16)
        self.assertEqual(navigation.last_page, 23685)
        self.assertTrue(navigation.has_next)
        self.assertFalse(navigation.can_prove_last_page)
        self.assertEqual(navigation.observed_board_ids, ["thesingularity", "thesingularity"])

    def test_empty_decorative_paging_container_does_not_hide_real_navigation(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            requested_page=1,
            expected_board_id="thesingularity",
        )
        parser.feed(
            '<div class="bottom_paging_box iconpaging"></div>' + pagination()
        )

        self.assertEqual(parser.navigation.paging_container_count, 2)
        self.assertEqual(parser.navigation.paging_container_closed_count, 2)
        self.assertTrue(parser.navigation.is_valid)

    def test_two_evidenced_paging_containers_are_ambiguous(self) -> None:
        parser = DcinsideListParser(BASE_URL, requested_page=1)
        parser.feed(pagination() + pagination())

        self.assertFalse(parser.navigation.is_valid)
        self.assertIn(
            "multiple_current_pages",
            [error.code for error in parser.navigation.errors],
        )

    def test_exact_korean_text_is_a_fallback_when_classes_are_absent(self) -> None:
        parser = DcinsideListParser(BASE_URL, requested_page=1)
        parser.feed(pagination(use_classes=False))

        self.assertTrue(parser.navigation.is_valid)
        self.assertEqual(parser.navigation.next_page, 16)
        self.assertEqual(parser.navigation.last_page, 23685)

    def test_same_page_end_link_without_next_can_prove_last_page(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            requested_page=7,
            expected_board_id="thesingularity",
        )
        parser.feed(
            """
            <div class="bottom_paging_box">
              <em>7</em>
              <a class="page_end" href="?id=thesingularity&amp;page=7">\ub05d</a>
            </div>
            """
        )

        self.assertTrue(parser.navigation.is_valid)
        self.assertTrue(parser.navigation.can_prove_last_page)

    def test_board_mismatch_invalidates_navigation_but_not_post_coverage(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            now=FIXED_NOW,
            requested_page=1,
            expected_board_id="thesingularity",
        )
        parser.feed(regular_row("105") + pagination(board_id="other"))

        self.assertTrue(parser.diagnostics.is_coverage_safe)
        self.assertFalse(parser.navigation.is_valid)
        self.assertIn(
            "navigation_board_mismatch",
            [error.code for error in parser.navigation.errors],
        )

    def test_requested_page_mismatch_is_navigation_only(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            now=FIXED_NOW,
            requested_page=2,
            expected_board_id="thesingularity",
        )
        parser.feed(regular_row("105") + pagination(current=1))

        self.assertTrue(parser.diagnostics.is_coverage_safe)
        self.assertFalse(parser.navigation.is_valid)
        self.assertIn(
            "requested_page_mismatch",
            [error.code for error in parser.navigation.errors],
        )

    def test_expected_board_must_agree_with_base_url(self) -> None:
        parser = DcinsideListParser(
            BASE_URL,
            now=FIXED_NOW,
            requested_page=1,
            expected_board_id="other",
        )
        parser.feed(regular_row("105") + pagination(board_id="other"))

        self.assertTrue(parser.diagnostics.is_coverage_safe)
        self.assertFalse(parser.navigation.is_valid)
        self.assertIn(
            "invalid_expected_board_id",
            [error.code for error in parser.navigation.errors],
        )

    def test_invalid_href_page_and_conflicting_links_are_fail_safe(self) -> None:
        parser = DcinsideListParser(BASE_URL, requested_page=1)
        parser.feed(
            """
            <div class="bottom_paging_box">
              <em>1</em>
              <a class="page_next" href="?id=thesingularity&amp;page=two">\ub2e4\uc74c</a>
              <a class="page_end" href="?id=thesingularity&amp;page=10">\ub05d</a>
              <a class="page_end" href="?id=thesingularity&amp;page=11">\ub05d</a>
            </div>
            """
        )

        self.assertFalse(parser.navigation.is_valid)
        self.assertIsNone(parser.navigation.next_page)
        self.assertIsNone(parser.navigation.last_page)
        codes = [error.code for error in parser.navigation.errors]
        self.assertIn("invalid_navigation_page", codes)
        self.assertIn("multiple_last_links", codes)

    def test_navigation_labels_outside_expected_container_are_ignored(self) -> None:
        parser = DcinsideListParser(BASE_URL, now=FIXED_NOW, requested_page=1)
        parser.feed(
            regular_row("105")
            + """
              <em>1</em>
              <a class="page_end" href="?id=thesingularity&amp;page=1">\ub05d</a>
            """
        )

        self.assertTrue(parser.diagnostics.is_coverage_safe)
        self.assertFalse(parser.navigation.paging_container_seen)
        self.assertFalse(parser.navigation.is_valid)
        self.assertIsNone(parser.navigation.last_page)

    def test_class_text_role_conflict_invalidates_navigation(self) -> None:
        parser = DcinsideListParser(BASE_URL, requested_page=1)
        parser.feed(
            """
            <div class="bottom_paging_box">
              <em>1</em>
              <a class="page_next" href="?id=thesingularity&amp;page=10">\ub05d</a>
            </div>
            """
        )

        self.assertFalse(parser.navigation.is_valid)
        self.assertIn(
            "conflicting_navigation_role",
            [error.code for error in parser.navigation.errors],
        )

    def test_contradictory_page_relationships_are_invalid(self) -> None:
        fixtures = (
            (
                "next_page_not_after_current",
                """
                <div class="bottom_paging_box">
                  <em>5</em>
                  <a class="page_next" href="?id=thesingularity&amp;page=5">\ub2e4\uc74c</a>
                  <a class="page_end" href="?id=thesingularity&amp;page=10">\ub05d</a>
                </div>
                """,
            ),
            (
                "last_page_before_current",
                """
                <div class="bottom_paging_box">
                  <em>5</em>
                  <a class="page_end" href="?id=thesingularity&amp;page=4">\ub05d</a>
                </div>
                """,
            ),
            (
                "next_page_after_last",
                """
                <div class="bottom_paging_box">
                  <em>5</em>
                  <a class="page_next" href="?id=thesingularity&amp;page=11">\ub2e4\uc74c</a>
                  <a class="page_end" href="?id=thesingularity&amp;page=10">\ub05d</a>
                </div>
                """,
            ),
        )

        for expected_code, html in fixtures:
            with self.subTest(expected_code=expected_code):
                parser = DcinsideListParser(BASE_URL, requested_page=5)
                parser.feed(html)
                self.assertFalse(parser.navigation.is_valid)
                self.assertIn(
                    expected_code,
                    [error.code for error in parser.navigation.errors],
                )

    def test_unclosed_pagination_is_invalid_and_close_reports_eof(self) -> None:
        parser = DcinsideListParser(BASE_URL, requested_page=1)
        parser.feed(
            """
            <div class="bottom_paging_box">
              <em>1</em>
              <a class="page_end" href="?id=thesingularity&amp;page=1">\ub05d</a>
            """
        )

        self.assertFalse(parser.navigation.is_valid)
        self.assertEqual(parser.navigation.paging_container_closed_count, 0)
        parser.close()
        self.assertFalse(parser.navigation.is_valid)
        self.assertIn(
            "unterminated_paging_container",
            [error.code for error in parser.navigation.errors],
        )


if __name__ == "__main__":
    unittest.main()
