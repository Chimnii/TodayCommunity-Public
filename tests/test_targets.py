from __future__ import annotations

import unittest

from crawler.targets import ARCHIVES, TARGETS, canonical_post_key, get_target


class TargetRegistryTests(unittest.TestCase):
    def test_six_collection_sources_map_to_four_public_archives(self) -> None:
        self.assertEqual(
            set(TARGETS),
            {
                "dcinside-singularity",
                "dcinside-ai-utilize",
                "dcinside-zeus-pride",
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
                "dcinside-zeus-pride",
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

    def test_thresholds_and_collection_policies_match_each_source(self) -> None:
        agent_stack = get_target("dcinside-ai-utilize")
        zeus = get_target("dcinside-zeus-pride")
        bayern = get_target("fmkorea-bayern-board")

        self.assertEqual((agent_stack.min_upvotes, agent_stack.min_comments), (4, 40))
        self.assertEqual((zeus.min_upvotes, zeus.min_comments), (3, 0))
        self.assertEqual(zeus.policy, "upvotes-only")
        self.assertEqual(zeus.subject_cell_mode, "absent")
        self.assertEqual(agent_stack.subject_cell_mode, "required")
        self.assertEqual((bayern.min_upvotes, bayern.min_comments), (13, 130))
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
            canonical_post_key(get_target("dcinside-zeus-pride"), "123"),
            "dcinside:zeusthegodofpride:123",
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

    def test_zeus_gallery_has_an_independent_public_archive(self) -> None:
        target = get_target("dcinside-zeus-pride")
        archive = ARCHIVES["dcinside-zeus-pride"]

        self.assertEqual(target.archive_key, archive.key)
        self.assertEqual(archive.display_name, "제우스 오만의 신")
        self.assertEqual(target.board_name, "제우스 오만의 신 마이너 갤러리")
        self.assertEqual(
            target.page_url(1),
            "https://gall.dcinside.com/mgallery/board/lists/"
            "?id=zeusthegodofpride",
        )
        self.assertEqual(
            target.page_url(2),
            "https://gall.dcinside.com/mgallery/board/lists/"
            "?id=zeusthegodofpride&page=2",
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
