from __future__ import annotations

import unittest

from crawler.targets import ARCHIVES, TARGETS, canonical_post_key, get_target


class TargetRegistryTests(unittest.TestCase):
    def test_five_collection_sources_map_to_three_public_archives(self) -> None:
        self.assertEqual(
            set(TARGETS),
            {
                "dcinside-singularity",
                "dcinside-ai-utilize",
                "fmkorea-best-munich-search",
                "fmkorea-best-bayern-search",
                "fmkorea-bayern-board",
            },
        )
        self.assertEqual(
            set(ARCHIVES),
            {
                "dcinside-singularity",
                "dcinside-agent-stack",
                "fmkorea-munich",
            },
        )
        self.assertEqual(
            {
                get_target(target_key).archive_key
                for target_key in (
                    "fmkorea-best-munich-search",
                    "fmkorea-best-bayern-search",
                    "fmkorea-bayern-board",
                )
            },
            {"fmkorea-munich"},
        )

    def test_thresholds_encode_the_requested_weighted_scores(self) -> None:
        agent_stack = get_target("dcinside-ai-utilize")
        bayern = get_target("fmkorea-bayern-board")

        self.assertEqual((agent_stack.min_upvotes, agent_stack.min_comments), (10, 100))
        self.assertEqual((bayern.min_upvotes, bayern.min_comments), (15, 150))
        for target_key in (
            "fmkorea-best-munich-search",
            "fmkorea-best-bayern-search",
        ):
            with self.subTest(target=target_key):
                search = get_target(target_key)
                self.assertTrue(search.collect_all)
                self.assertEqual(search.policy, "collect-all")

    def test_fmkorea_hot_limits_are_source_specific(self) -> None:
        singularity = get_target("dcinside-singularity")
        expected = (
            singularity.hot_lookback_minutes,
            singularity.hot_max_seconds,
            singularity.hot_max_pages,
            singularity.request_interval_seconds,
        )
        self.assertEqual(expected, (180.0, 180.0, 30, 10.0))

        for target_key in (
            "fmkorea-best-munich-search",
            "fmkorea-best-bayern-search",
        ):
            with self.subTest(target=target_key):
                target = get_target(target_key)
                self.assertEqual(
                    (
                        target.hot_lookback_minutes,
                        target.hot_max_seconds,
                        target.hot_max_pages,
                        target.request_interval_seconds,
                    ),
                    expected,
                )

        bayern_board = get_target("fmkorea-bayern-board")
        self.assertEqual(
            (
                bayern_board.hot_lookback_minutes,
                bayern_board.hot_max_seconds,
                bayern_board.hot_max_pages,
                bayern_board.request_interval_seconds,
            ),
            (360.0, 180.0, 30, 10.0),
        )

    def test_site_specific_canonical_keys_share_only_when_intended(self) -> None:
        self.assertEqual(
            canonical_post_key(get_target("dcinside-singularity"), "123"),
            "dcinside:thesingularity:123",
        )
        self.assertEqual(
            canonical_post_key(get_target("dcinside-ai-utilize"), "123"),
            "dcinside:ai_utilize:123",
        )
        self.assertEqual(
            {
                canonical_post_key(get_target(target_key), "123")
                for target_key in (
                    "fmkorea-best-munich-search",
                    "fmkorea-best-bayern-search",
                    "fmkorea-bayern-board",
                )
            },
            {"fmkorea:123"},
        )

    def test_migrated_agent_archive_uses_an_independent_source_identity(self) -> None:
        migrated = get_target("dcinside-ai-utilize")
        archive = ARCHIVES["dcinside-agent-stack"]

        self.assertEqual(migrated.archive_key, "dcinside-agent-stack")
        self.assertEqual(archive.display_name, "AI 활용")
        self.assertEqual(archive.description, "디시인사이드 AI 활용 갤러리 인기글")
        self.assertEqual(migrated.board_name, "AI 활용 마이너 갤러리")
        self.assertIn("id=ai_utilize", migrated.page_url(1))
        self.assertIn("id=ai_utilize", migrated.page_url(2))
        self.assertNotIn("dcinside-agent-stack", TARGETS)

    def test_search_uses_its_special_first_page_url(self) -> None:
        for target_key in (
            "fmkorea-best-munich-search",
            "fmkorea-best-bayern-search",
        ):
            with self.subTest(target=target_key):
                target = get_target(target_key)
                self.assertIn("/search.php?", target.page_url(1))
                self.assertIn("/index.php?", target.page_url(2))
                self.assertIn("page=2", target.page_url(2))

        self.assertIn(
            "search_keyword=%EB%B0%94%EC%9D%B4%EC%97%90%EB%A5%B8",
            get_target("fmkorea-best-bayern-search").page_url(1),
        )


if __name__ == "__main__":
    unittest.main()
