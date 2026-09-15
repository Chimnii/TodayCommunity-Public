from __future__ import annotations

import os
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from crawler.collection_rules import SubjectCollectionRule
from crawler.jobs.run_all_sources import dc_cycle_config
from crawler.jobs.run_cycle import CYCLE_MODE_BACKFILL, CYCLE_MODE_HOT, CrawlCycle, count_qualifying
from crawler.jobs.scan_new_posts import scan_target, update_finalized_posts, upsert_source
from crawler.parsers.dcinside import DcinsideListParser, is_qualifying_post, meets_collection_threshold
from crawler.targets import get_target
from tests.test_dcinside_parser import NOTICE_ROW, regular_row
from tests.test_run_cycle import FIXED_NOW, MappingFetcher, runtime
from tests.test_scan_new_posts import SqliteClient


SUBJECTS = {
    "dcinside-singularity": ("📪정보", "☕작업잡담"),
    "dcinside-ai-utilize": ("📢정보", "📚활용", "📰뉴스", "⭐후기"),
    "dcinside-zeus-pride": ("정보", "공략"),
}


def parse_posts(target, html):
    parser = DcinsideListParser(
        target.board_url,
        now=FIXED_NOW,
        min_upvotes=target.min_upvotes,
        min_comments=target.min_comments,
        policy=target.policy,
        subject_cell_mode=target.subject_cell_mode,
        subject_rules=target.subject_rules,
    )
    board_id = target.board_url.split("id=")[1]
    parser.feed(html.replace("thesingularity", board_id))
    parser.close()
    return parser


class SubjectCollectionTests(unittest.TestCase):
    def test_all_dc_targets_use_score_five_and_three_hour_lookback(self):
        with patch.dict(os.environ, {}, clear=True):
            for key in SUBJECTS:
                target = get_target(key)
                with self.subTest(target=key):
                    self.assertEqual((target.min_upvotes, target.min_comments), (5, 50))
                    self.assertEqual(target.policy, "weighted-engagement")
                    self.assertEqual(target.hot_lookback_minutes, 180)
                    self.assertEqual(dc_cycle_config(target, CYCLE_MODE_HOT).hot_lookback_minutes, 180)
                    for votes in range(6):
                        boundary = (5 - votes) * 10
                        for comments in {max(0, boundary - 1), boundary}:
                            self.assertEqual(
                                meets_collection_threshold(votes, comments, target.min_upvotes, target.min_comments, target.policy),
                                10 * votes + comments >= 50,
                            )

    def test_all_eight_exact_subjects_qualify_without_engagement(self):
        for key, subjects in SUBJECTS.items():
            target = get_target(key)
            self.assertEqual(tuple(rule.subject for rule in target.subject_rules), subjects)
            for subject in subjects:
                with self.subTest(target=key, subject=subject):
                    parser = parse_posts(target, regular_row("100", subject=subject, upvotes="0"))
                    self.assertTrue(parser.diagnostics.is_collection_safe)
                    self.assertEqual(parser.posts[0].qualifies_by, "subject")
                    self.assertEqual(count_qualifying(parser.posts, target), 1)
                    self.assertTrue(is_qualifying_post(parser.posts[0], 5, 50, subject_rules=target.subject_rules))

    def test_subject_match_is_exact_and_does_not_use_title_or_other_gallery(self):
        target = get_target("dcinside-singularity")
        for subject in ("일반", "", "정보", "📢정보", "📪정보추가", "☕작업"):
            with self.subTest(subject=subject):
                parser = parse_posts(target, regular_row("100", subject=subject, title="📪정보 ☕작업잡담", upvotes="4", comments=9))
                self.assertEqual(parser.posts[0].qualifies_by, "none")
                self.assertEqual(count_qualifying(parser.posts, target), 0)

    def test_expanded_subject_qualifies_but_invalid_rows_and_notices_do_not(self):
        target = get_target("dcinside-singularity")
        expanded = regular_row(
            "100", upvotes="0",
            subject_markup='<td class="gall_subject">☕작업<p class="subject_inner" style="display:none">☕작업잡담</p></td>',
        )
        invalid = regular_row("99", subject="📪정보", upvotes="invalid")
        parser = parse_posts(target, expanded + invalid + NOTICE_ROW)
        self.assertEqual([post.external_post_id for post in parser.posts], ["100"])
        self.assertEqual(parser.posts[0].qualifies_by, "subject")
        self.assertFalse(parser.diagnostics.is_coverage_safe)

    def test_optional_subject_count_floors_are_independent_of_general_score(self):
        target = replace(get_target("dcinside-zeus-pride"), subject_rules=(SubjectCollectionRule("정보", min_upvotes=1, min_comments=2),))
        for votes, comments, subject, expected, reason in (
            (0, 0, "정보", False, "none"),
            (1, 1, "정보", False, "none"),
            (1, 2, "정보", True, "subject"),
            (0, 50, "정보", True, "comments"),
            (5, 0, "정보", True, "upvotes"),
            (1, 2, "일반", False, "none"),
        ):
            with self.subTest(votes=votes, comments=comments, subject=subject):
                parser = parse_posts(target, regular_row("100", subject=subject, upvotes=str(votes), comments=comments))
                self.assertEqual(count_qualifying(parser.posts, target), int(expected))
                self.assertEqual(parser.posts[0].qualifies_by, reason)
        for kwargs in ({"subject": " "}, {"subject": "정보", "min_upvotes": -1}, {"subject": "정보", "min_comments": 1.5}):
            with self.assertRaises(ValueError):
                SubjectCollectionRule(**kwargs)

    def test_hot_backfill_and_direct_scan_store_the_same_union_once(self):
        for key, subjects in SUBJECTS.items():
            target = get_target(key)
            for mode in (CYCLE_MODE_HOT, CYCLE_MODE_BACKFILL):
                with self.subTest(target=key, mode=mode):
                    date = "2026-07-16 18:00:00" if mode == CYCLE_MODE_HOT else "2026-07-16 07:00:00"
                    html = "".join(regular_row(str(100 - i), subject=subject, upvotes="0", date_title=date) for i, subject in enumerate(subjects))
                    html += regular_row("90", subject=subjects[0], upvotes="5", date_title=date)
                    html += regular_row("89", subject="일반", upvotes="3", comments=20, date_title=date)
                    html += regular_row("88", subject="일반", upvotes="4", comments=9, date_title=date)
                    board_id = target.board_url.split("id=")[1]
                    source_fetcher = MappingFetcher({1: html}, last_page=1)

                    def fetcher(url, timeout_seconds):
                        return source_fetcher(url, timeout_seconds).replace("thesingularity", board_id)

                    client = SqliteClient()
                    settings = dc_cycle_config(target, mode)
                    cycle = CrawlCycle(target=target, config=settings, runtime=runtime(settings), client=client, fetcher=fetcher, cycle_started_at=FIXED_NOW, mode=mode)
                    result = cycle.run()
                    self.assertEqual(result["status"], "completed")
                    expected_ids = {str(100 - i) for i in range(len(subjects))} | {"90", "89"}
                    stored = client.query("SELECT external_post_id, qualifies_by FROM posts")
                    self.assertEqual({row["external_post_id"] for row in stored}, expected_ids)
                    self.assertEqual(len(stored), len(expected_ids))
                    self.assertEqual(sum(row["qualifies_by"] == "subject" for row in stored), len(subjects) + 1)
                    self.assertEqual(sum(phase["matched_posts"] for phase in result["phases"]), len(expected_ids))
                    with patch("crawler.jobs.scan_new_posts.fetch_html", return_value=html.replace("thesingularity", board_id)):
                        direct = scan_target(target, pages=1, page_delay_seconds=0)
                    self.assertEqual({post["external_post_id"] for post in direct["posts"]}, expected_ids)

    def test_rule_changes_preserve_posts_and_completed_coverage(self):
        target = get_target("dcinside-singularity")
        client = SqliteClient()
        settings = dc_cycle_config(target, CYCLE_MODE_BACKFILL)
        cycle = CrawlCycle(target=target, config=settings, runtime=runtime(settings), client=client, cycle_started_at=FIXED_NOW, mode=CYCLE_MODE_BACKFILL)
        html = regular_row("100", subject="📪정보", upvotes="0", date_title="2026-07-15 07:00:00")
        posts = parse_posts(target, html).posts
        cycle._commit_finalized_page(posts)
        before_posts = client.query("SELECT * FROM posts")
        before_coverage = client.query("SELECT * FROM coverage_intervals")
        changed = replace(target, subject_rules=(SubjectCollectionRule("새말머리"),))
        upsert_source(client, changed, "2026-07-16T12:01:00Z")
        self.assertEqual(client.query("SELECT * FROM posts"), before_posts)
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), before_coverage)
        observation = parse_posts(changed, html + regular_row("99", subject="📪정보", upvotes="0")).posts
        update_finalized_posts(client, changed, [asdict(post) for post in observation], "2026-07-16T12:02:00Z")
        self.assertEqual(client.query("SELECT external_post_id, status FROM posts"), [{"external_post_id": "100", "status": "active"}])
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), before_coverage)


if __name__ == "__main__":
    unittest.main()
