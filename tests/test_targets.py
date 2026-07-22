from __future__ import annotations

import unittest

from crawler.targets import ARCHIVES, TARGETS, canonical_post_key, get_target


class TargetRegistryTests(unittest.TestCase):
    def test_five_collection_sources_map_to_three_public_archives(self) -> None:
        self.assertEqual(
            set(TARGETS),
            {
                "dcinside-singularity",
                "dcinside-agent-stack",
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
        agent_stack = get_target("dcinside-agent-stack")
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

    def test_fmkorea_hot_limits_match_singularity(self) -> None:
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
            "fmkorea-bayern-board",
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

    def test_site_specific_canonical_keys_share_only_when_intended(self) -> None:
        self.assertEqual(
            canonical_post_key(get_target("dcinside-singularity"), "123"),
            "dcinside:thesingularity:123",
        )
        self.assertEqual(
            canonical_post_key(get_target("dcinside-agent-stack"), "123"),
            "dcinside:agent_stack:123",
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
