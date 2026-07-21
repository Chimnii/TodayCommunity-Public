from __future__ import annotations

import unittest

from crawler.targets import ARCHIVES, TARGETS, canonical_post_key, get_target


class TargetRegistryTests(unittest.TestCase):
    def test_four_collection_sources_map_to_three_public_archives(self) -> None:
        self.assertEqual(
            set(TARGETS),
            {
                "dcinside-singularity",
                "dcinside-agent-stack",
                "fmkorea-best-munich-search",
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
            get_target("fmkorea-best-munich-search").archive_key,
            get_target("fmkorea-bayern-board").archive_key,
        )

    def test_thresholds_encode_the_requested_weighted_scores(self) -> None:
        agent_stack = get_target("dcinside-agent-stack")
        bayern = get_target("fmkorea-bayern-board")

        self.assertEqual((agent_stack.min_upvotes, agent_stack.min_comments), (10, 100))
        self.assertEqual((bayern.min_upvotes, bayern.min_comments), (15, 150))
        self.assertEqual(bayern.hot_lookback_minutes, 720)
        search = get_target("fmkorea-best-munich-search")
        self.assertTrue(search.collect_all)
        self.assertEqual(search.policy, "collect-all")

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
            canonical_post_key(get_target("fmkorea-best-munich-search"), "123"),
            canonical_post_key(get_target("fmkorea-bayern-board"), "123"),
        )

    def test_search_uses_its_special_first_page_url(self) -> None:
        target = get_target("fmkorea-best-munich-search")

        self.assertIn("/search.php?", target.page_url(1))
        self.assertIn("/index.php?", target.page_url(2))
        self.assertIn("page=2", target.page_url(2))


if __name__ == "__main__":
    unittest.main()
