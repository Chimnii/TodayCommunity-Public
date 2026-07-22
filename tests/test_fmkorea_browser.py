from __future__ import annotations

import os
import signal
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.fmkorea_browser import (
    CHROME_EXECUTABLE_CANDIDATES,
    DEFAULT_CDP_PORT,
    PROFILE_MARKER_CONTENT,
    PROFILE_MARKER_NAME,
    FmkoreaBrowserConfig,
    FmkoreaBrowserStartupError,
    FmkoreaChromeSession,
    HostSessionLock,
    assert_cdp_port_available,
    is_cdp_endpoint_ready,
    prepare_dedicated_profile,
    validate_fmkorea_url,
)
from crawler.jobs.scan_new_posts import (
    CrawlBlockedError,
    CrawlSourceError,
    CrawlTimeoutError,
    CrawlTransientError,
)


class FakePlaywrightError(Exception):
    pass


class FakePlaywrightTimeout(FakePlaywrightError):
    pass


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        content_type: str = "text/html; charset=UTF-8",
        body: str = "<html><body>normal list</body></html>",
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body

    def header_value(self, name: str) -> str:
        return self.content_type if name.lower() == "content-type" else ""

    def text(self) -> str:
        return self.body


class FakePage:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        final_url: str = "https://www.fmkorea.com/index.php?mid=best",
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.url = final_url
        self.error = error
        self.calls: list[tuple[str, str, int]] = []

    def is_closed(self) -> bool:
        return False

    def goto(self, url: str, *, wait_until: str, timeout: int):
        self.calls.append((url, wait_until, timeout))
        if self.error:
            raise self.error
        return self.response


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def attached_session(
    page: FakePage,
    *,
    clock: FakeClock | None = None,
) -> FmkoreaChromeSession:
    config = FmkoreaBrowserConfig(
        profile_dir=Path("unused-profile"),
        chrome_executable_path=Path("unused-chrome.exe"),
    )
    session = FmkoreaChromeSession(
        config,
        monotonic=clock.monotonic if clock else time.monotonic,
        sleep=clock.sleep if clock else time.sleep,
    )
    session._page = page
    session._playwright_timeout_error = FakePlaywrightTimeout
    session._playwright_error = FakePlaywrightError
    return session


class FmkoreaBrowserConfigTests(unittest.TestCase):
    def test_defaults_support_hosted_linux_chrome_and_ten_second_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chrome = Path(temp_dir) / "chrome"
            chrome.touch()
            with patch.dict(
                os.environ,
                {
                    "TC_FMKOREA_CHROME_PATH": str(chrome),
                    "TC_FMKOREA_PROFILE_DIR": str(Path(temp_dir) / "profile"),
                },
                clear=True,
            ):
                config = FmkoreaBrowserConfig.from_env(headless=True)

        self.assertEqual(config.min_navigation_interval_seconds, 10.0)
        self.assertIn(Path("/usr/bin/google-chrome"), CHROME_EXECUTABLE_CANDIDATES)

    def test_env_config_uses_explicit_dedicated_paths_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chrome = root / "chrome.exe"
            chrome.touch()
            profile = root / "fm-profile"
            with patch.dict(
                os.environ,
                {
                    "TC_FMKOREA_CHROME_PATH": str(chrome),
                    "TC_FMKOREA_PROFILE_DIR": str(profile),
                    "TC_FMKOREA_CDP_PORT": "40123",
                    "TC_FMKOREA_HEADLESS": "yes",
                    "TC_FMKOREA_STARTUP_TIMEOUT_SECONDS": "12.5",
                    "TC_FMKOREA_REQUEST_INTERVAL_SECONDS": "21",
                },
                clear=False,
            ):
                config = FmkoreaBrowserConfig.from_env()

        self.assertEqual(config.profile_dir, profile.resolve())
        self.assertEqual(config.chrome_executable_path, chrome.resolve())
        self.assertEqual(config.cdp_port, 40123)
        self.assertTrue(config.headless)
        self.assertEqual(config.startup_timeout_seconds, 12.5)
        self.assertEqual(config.min_navigation_interval_seconds, 21.0)
        self.assertEqual(config.cdp_endpoint, "http://127.0.0.1:40123")

    def test_cli_headless_override_wins_over_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chrome = root / "chrome.exe"
            chrome.touch()
            with patch.dict(
                os.environ,
                {
                    "TC_FMKOREA_CHROME_PATH": str(chrome),
                    "TC_FMKOREA_PROFILE_DIR": str(root / "profile"),
                    "TC_FMKOREA_CDP_PORT": str(DEFAULT_CDP_PORT),
                    "TC_FMKOREA_HEADLESS": "1",
                },
                clear=False,
            ):
                config = FmkoreaBrowserConfig.from_env(headless=False)
        self.assertFalse(config.headless)

    def test_invalid_port_fails_before_browser_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chrome = Path(temp_dir) / "chrome.exe"
            chrome.touch()
            with patch.dict(
                os.environ,
                {
                    "TC_FMKOREA_CHROME_PATH": str(chrome),
                    "TC_FMKOREA_CDP_PORT": "80",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "between 1024 and 65535"):
                    FmkoreaBrowserConfig.from_env()


class DedicatedProfileTests(unittest.TestCase):
    def test_new_profile_gets_ownership_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "profile"
            prepared = prepare_dedicated_profile(profile)
            marker = prepared / PROFILE_MARKER_NAME
            self.assertEqual(marker.read_text(encoding="utf-8"), PROFILE_MARKER_CONTENT)

    def test_nonempty_unmarked_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "profile"
            profile.mkdir()
            (profile / "Preferences").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                FmkoreaBrowserStartupError,
                "non-empty unmarked Chrome profile",
            ):
                prepare_dedicated_profile(profile)

    def test_normal_personal_chrome_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_app_data = Path(temp_dir)
            personal = local_app_data / "Google" / "Chrome" / "User Data" / "Default"
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local_app_data)},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    FmkoreaBrowserStartupError,
                    "normal Chrome User Data",
                ):
                    prepare_dedicated_profile(personal)

    def test_occupied_cdp_port_is_rejected(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        try:
            with self.assertRaisesRegex(FmkoreaBrowserStartupError, "already in use"):
                assert_cdp_port_available(port)
        finally:
            listener.close()


class CdpReadinessTests(unittest.TestCase):
    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self) -> bytes:
            return self.body

    def test_requires_loopback_browser_websocket_on_the_same_port(self) -> None:
        valid = self.Response(
            b'{"webSocketDebuggerUrl": '
            b'"ws://localhost:39224/devtools/browser/synthetic"}'
        )
        self.assertTrue(
            is_cdp_endpoint_ready(
                "http://127.0.0.1:39224",
                open_url=lambda *args, **kwargs: valid,
            )
        )

        for body in (
            b"[]",
            b'{"webSocketDebuggerUrl":"ws://example.com:39224/devtools/browser/x"}',
            b'{"webSocketDebuggerUrl":"ws://localhost:40000/devtools/browser/x"}',
            b"not-json",
        ):
            with self.subTest(body=body):
                response = self.Response(body)
                self.assertFalse(
                    is_cdp_endpoint_ready(
                        "http://127.0.0.1:39224",
                        open_url=lambda *args, **kwargs: response,
                    )
                )


class ChromeLifecycleTests(unittest.TestCase):
    def test_close_sends_explicit_browser_close_and_stops_playwright(self) -> None:
        events = []

        class CdpSession:
            def send(self, method: str) -> None:
                events.append(("send", method))

            def detach(self) -> None:
                events.append(("detach", None))

        class Browser:
            def new_browser_cdp_session(self):
                return CdpSession()

            def close(self) -> None:
                events.append(("browser_close", None))

        class Playwright:
            def stop(self) -> None:
                events.append(("playwright_stop", None))

        class Process:
            def poll(self):
                events.append(("poll", None))
                return 0

        session = FmkoreaChromeSession(
            FmkoreaBrowserConfig(
                profile_dir=Path("synthetic-profile"),
                chrome_executable_path=Path("synthetic-chrome.exe"),
            )
        )
        session._browser = Browser()
        session._playwright = Playwright()
        session._process = Process()

        session.close()

        self.assertEqual(events[0], ("send", "Browser.close"))
        self.assertIn(("browser_close", None), events)
        self.assertIn(("playwright_stop", None), events)
        self.assertIn(("poll", None), events)
        self.assertEqual(session.cleanup_warnings, [])

    def test_host_lock_rejects_a_second_session_until_release(self) -> None:
        first = HostSessionLock()
        second = HostSessionLock()
        first.acquire()
        try:
            with self.assertRaisesRegex(
                FmkoreaBrowserStartupError,
                "already running",
            ):
                second.acquire()
        finally:
            first.release()

        third = HostSessionLock()
        third.acquire()
        third.release()

    def test_posix_cleanup_stops_the_owned_process_group(self) -> None:
        class Process:
            pid = 43210

            def poll(self):
                return 0

        session = FmkoreaChromeSession(
            FmkoreaBrowserConfig(
                profile_dir=Path("synthetic-profile"),
                chrome_executable_path=Path("synthetic-chrome"),
            )
        )
        with (
            patch("crawler.fmkorea_browser.os.name", "posix"),
            patch(
                "crawler.fmkorea_browser._posix_process_group_exists",
                side_effect=[True, False],
            ),
            patch(
                "crawler.fmkorea_browser.os.killpg",
                create=True,
            ) as kill_group,
            patch(
                "crawler.fmkorea_browser.signal.SIGKILL",
                9,
                create=True,
            ),
        ):
            session._stop_owned_process_tree(Process())

        kill_group.assert_called_once_with(43210, signal.SIGTERM)
        self.assertEqual(session.cleanup_warnings, [])


class FmkoreaChromeFetchTests(unittest.TestCase):
    def test_normal_html_uses_domcontentloaded_and_returns_body(self) -> None:
        response = FakeResponse(body="<html><body>FM rows</body></html>")
        page = FakePage(response)
        session = attached_session(page, clock=FakeClock())

        html = session("https://www.fmkorea.com/search.php?mid=best", 7.25)

        self.assertEqual(html, response.body)
        self.assertEqual(
            page.calls,
            [
                (
                    "https://www.fmkorea.com/search.php?mid=best",
                    "domcontentloaded",
                    7250,
                )
            ],
        )

    def test_request_and_redirect_are_both_origin_allowlisted(self) -> None:
        with self.assertRaisesRegex(CrawlSourceError, "allowed HTTPS origin"):
            validate_fmkorea_url("https://example.com/", label="request")

        page = FakePage(
            FakeResponse(),
            final_url="https://challenge.example.net/blocked",
        )
        session = attached_session(page)
        with self.assertRaisesRegex(CrawlBlockedError, "redirected.*outside"):
            session("https://www.fmkorea.com/index.php?mid=best", 5)

    def test_block_statuses_and_challenge_html_stop_immediately(self) -> None:
        for status in (403, 429, 430):
            with self.subTest(status=status):
                session = attached_session(FakePage(FakeResponse(status=status)))
                with self.assertRaisesRegex(CrawlBlockedError, f"HTTP {status}"):
                    session("https://www.fmkorea.com/index.php?mid=best", 5)

        challenge = FakeResponse(
            body='<html><script src="/cdn-cgi/challenge-platform/x"></script></html>'
        )
        session = attached_session(FakePage(challenge))
        with self.assertRaisesRegex(CrawlBlockedError, "browser challenge"):
            session("https://www.fmkorea.com/index.php?mid=best", 5)

    def test_non_html_and_other_http_fail_closed(self) -> None:
        session = attached_session(
            FakePage(FakeResponse(content_type="application/json"))
        )
        with self.assertRaisesRegex(CrawlSourceError, "was not HTML"):
            session("https://www.fmkorea.com/index.php?mid=best", 5)

        session = attached_session(FakePage(FakeResponse(status=404)))
        with self.assertRaisesRegex(CrawlSourceError, "HTTP 404"):
            session("https://www.fmkorea.com/index.php?mid=best", 5)

    def test_timeout_and_browser_transport_errors_are_typed(self) -> None:
        timeout_session = attached_session(
            FakePage(error=FakePlaywrightTimeout("synthetic timeout"))
        )
        with self.assertRaises(CrawlTimeoutError):
            timeout_session("https://www.fmkorea.com/index.php?mid=best", 5)

        error_session = attached_session(
            FakePage(error=FakePlaywrightError("synthetic browser failure"))
        )
        with self.assertRaises(CrawlTransientError):
            error_session("https://www.fmkorea.com/index.php?mid=best", 5)

    def test_one_session_enforces_origin_spacing_across_feed_boundaries(self) -> None:
        clock = FakeClock()
        page = FakePage(FakeResponse())
        session = attached_session(page, clock=clock)

        session("https://www.fmkorea.com/search.php?mid=best", 30)
        session("https://www.fmkorea.com/index.php?mid=football_world", 30)

        self.assertEqual(clock.sleeps, [10.0])
        self.assertEqual(len(page.calls), 2)
        self.assertEqual(page.calls[1][2], 20000)


if __name__ == "__main__":
    unittest.main()
