from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib import request
from urllib.parse import urlparse

from crawler.jobs.scan_new_posts import (
    CrawlBlockedError,
    CrawlSourceError,
    CrawlTimeoutError,
    CrawlTransientError,
    detect_blocked_html,
)


FMKOREA_ALLOWED_HOSTS = frozenset({"fmkorea.com", "www.fmkorea.com"})
DEFAULT_CDP_PORT = 39224
PROFILE_MARKER_NAME = ".todaycommunity-fmkorea-profile"
PROFILE_MARKER_CONTENT = "TodayCommunity FMKorea dedicated Chrome profile\n"
HOST_SESSION_MUTEX_NAME = r"Global\TodayCommunity.FMKorea.Chrome"
CHROME_EXECUTABLE_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
    Path("/opt/google/chrome/google-chrome"),
)
LOCAL_CDP_OPENER = request.build_opener(request.ProxyHandler({}))
LOCAL_SESSION_LOCK = threading.Lock()


class FmkoreaBrowserDependencyError(RuntimeError):
    """Raised when the optional local-browser dependency is unavailable."""


class FmkoreaBrowserStartupError(RuntimeError):
    """Raised before a source request when dedicated Chrome cannot start safely."""


class HostSessionLock:
    """Hold one FMKorea Chrome session across processes on the current host."""

    def __init__(self) -> None:
        self._local_acquired = False
        self._windows_handle = None
        self._posix_file = None

    def acquire(self) -> None:
        if self._local_acquired:
            return
        if not LOCAL_SESSION_LOCK.acquire(blocking=False):
            raise FmkoreaBrowserStartupError(
                "Another FMKorea Chrome session is already running in this process."
            )
        self._local_acquired = True
        try:
            if os.name == "nt":
                self._acquire_windows_mutex()
            else:
                self._acquire_posix_lock()
        except Exception:
            LOCAL_SESSION_LOCK.release()
            self._local_acquired = False
            raise

    def release(self) -> None:
        release_error: Optional[Exception] = None
        if self._windows_handle is not None:
            try:
                self._release_windows_mutex()
            except Exception as exc:
                release_error = exc
        if self._posix_file is not None:
            try:
                self._release_posix_lock()
            except Exception as exc:
                release_error = release_error or exc
        if self._local_acquired:
            LOCAL_SESSION_LOCK.release()
            self._local_acquired = False
        if release_error is not None:
            raise release_error

    def _acquire_windows_mutex(self) -> None:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        create_mutex.restype = ctypes.c_void_p
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wait_for_single_object.restype = ctypes.c_uint32
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool

        handle = create_mutex(None, False, HOST_SESSION_MUTEX_NAME)
        if not handle:
            raise FmkoreaBrowserStartupError(
                "Could not create the host-wide FMKorea Chrome mutex."
            )
        wait_result = wait_for_single_object(handle, 0)
        if wait_result not in {0x00000000, 0x00000080}:
            close_handle(handle)
            if wait_result == 0x00000102:
                raise FmkoreaBrowserStartupError(
                    "Another FMKorea Chrome session is already running on this host."
                )
            raise FmkoreaBrowserStartupError(
                f"Could not acquire the FMKorea Chrome mutex (wait={wait_result})."
            )
        self._windows_handle = handle

    def _release_windows_mutex(self) -> None:
        import ctypes

        handle = self._windows_handle
        self._windows_handle = None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        release_mutex = kernel32.ReleaseMutex
        release_mutex.argtypes = [ctypes.c_void_p]
        release_mutex.restype = ctypes.c_bool
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool
        released = bool(release_mutex(handle))
        closed = bool(close_handle(handle))
        if not released or not closed:
            raise RuntimeError("Could not release the host-wide FMKorea Chrome mutex.")

    def _acquire_posix_lock(self) -> None:
        import fcntl

        lock_dir = Path(tempfile.gettempdir()) / "todaycommunity"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = (lock_dir / "fmkorea-chrome.lock").open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            raise FmkoreaBrowserStartupError(
                "Another FMKorea Chrome session is already running on this host."
            ) from exc
        self._posix_file = lock_file

    def _release_posix_lock(self) -> None:
        import fcntl

        lock_file = self._posix_file
        self._posix_file = None
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


@dataclass(frozen=True)
class FmkoreaBrowserConfig:
    profile_dir: Path
    chrome_executable_path: Path
    cdp_port: int = DEFAULT_CDP_PORT
    headless: bool = False
    startup_timeout_seconds: float = 15.0
    min_navigation_interval_seconds: float = 10.0

    @property
    def cdp_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"

    @classmethod
    def from_env(
        cls,
        *,
        headless: Optional[bool] = None,
    ) -> "FmkoreaBrowserConfig":
        configured_profile = os.getenv("TC_FMKOREA_PROFILE_DIR", "").strip()
        configured_chrome = os.getenv("TC_FMKOREA_CHROME_PATH", "").strip()
        configured_port = os.getenv("TC_FMKOREA_CDP_PORT", "").strip()
        configured_timeout = os.getenv(
            "TC_FMKOREA_STARTUP_TIMEOUT_SECONDS", ""
        ).strip()
        configured_interval = os.getenv(
            "TC_FMKOREA_REQUEST_INTERVAL_SECONDS", ""
        ).strip()
        if headless is None:
            headless = _is_truthy(os.getenv("TC_FMKOREA_HEADLESS", "0"))

        try:
            cdp_port = int(configured_port or DEFAULT_CDP_PORT)
        except ValueError as exc:
            raise ValueError("TC_FMKOREA_CDP_PORT must be an integer") from exc
        if not 1024 <= cdp_port <= 65535:
            raise ValueError("TC_FMKOREA_CDP_PORT must be between 1024 and 65535")

        try:
            startup_timeout = float(configured_timeout or 15.0)
        except ValueError as exc:
            raise ValueError(
                "TC_FMKOREA_STARTUP_TIMEOUT_SECONDS must be numeric"
            ) from exc
        if not 1.0 <= startup_timeout <= 60.0:
            raise ValueError(
                "TC_FMKOREA_STARTUP_TIMEOUT_SECONDS must be between 1 and 60"
            )

        try:
            request_interval = float(configured_interval or 10.0)
        except ValueError as exc:
            raise ValueError(
                "TC_FMKOREA_REQUEST_INTERVAL_SECONDS must be numeric"
            ) from exc
        if not 1.0 <= request_interval <= 300.0:
            raise ValueError(
                "TC_FMKOREA_REQUEST_INTERVAL_SECONDS must be between 1 and 300"
            )

        return cls(
            profile_dir=resolve_profile_dir(configured_profile),
            chrome_executable_path=resolve_chrome_executable(configured_chrome),
            cdp_port=cdp_port,
            headless=bool(headless),
            startup_timeout_seconds=startup_timeout,
            min_navigation_interval_seconds=request_interval,
        )


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_profile_dir(configured_path: str = "") -> Path:
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        root = Path(local_app_data)
    else:
        root = Path.home() / ".local" / "share"
    return (root / "TodayCommunity" / "fmkorea-chrome-profile").resolve()


def resolve_chrome_executable(configured_path: str = "") -> Path:
    if configured_path:
        candidate = Path(configured_path).expanduser().resolve()
        if not candidate.is_file():
            raise FmkoreaBrowserStartupError(
                f"Configured Chrome executable does not exist: {candidate}"
            )
        return candidate

    candidates = list(CHROME_EXECUTABLE_CANDIDATES)
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FmkoreaBrowserStartupError(
        "Google Chrome was not found. Set TC_FMKOREA_CHROME_PATH to its executable."
    )


def prepare_dedicated_profile(profile_dir: Path) -> Path:
    resolved = profile_dir.expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        personal_root = (
            Path(local_app_data) / "Google" / "Chrome" / "User Data"
        ).resolve()
        if resolved == personal_root or personal_root in resolved.parents:
            raise FmkoreaBrowserStartupError(
                "TC_FMKOREA_PROFILE_DIR must not use the normal Chrome User Data "
                "directory or one of its profiles."
            )

    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise FmkoreaBrowserStartupError(
            "TC_FMKOREA_PROFILE_DIR must be a dedicated subdirectory."
        )
    if resolved.exists() and not resolved.is_dir():
        raise FmkoreaBrowserStartupError(
            f"Chrome profile path is not a directory: {resolved}"
        )

    marker = resolved / PROFILE_MARKER_NAME
    if resolved.exists():
        entries = list(resolved.iterdir())
        if entries and not marker.is_file():
            raise FmkoreaBrowserStartupError(
                "Refusing to automate a non-empty unmarked Chrome profile directory: "
                f"{resolved}"
            )
    else:
        resolved.mkdir(parents=True, exist_ok=False)
    if not marker.exists():
        marker.write_text(PROFILE_MARKER_CONTENT, encoding="utf-8")
    elif marker.read_text(encoding="utf-8") != PROFILE_MARKER_CONTENT:
        raise FmkoreaBrowserStartupError(
            f"Unexpected FMKorea Chrome profile marker: {marker}"
        )
    return resolved


def assert_cdp_port_available(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise FmkoreaBrowserStartupError(
            f"FMKorea Chrome CDP port {port} is already in use. "
            "Another local browser crawl may still be running."
        ) from exc
    finally:
        probe.close()


def is_loopback_port_listening(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        return probe.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        probe.close()


def wait_for_cdp_listener_to_stop(
    port: int,
    *,
    timeout_seconds: float = 3.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = monotonic() + timeout_seconds
    while is_loopback_port_listening(port):
        if monotonic() >= deadline:
            return False
        sleep(0.1)
    return True


def _posix_process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_cdp_endpoint_ready(
    endpoint: str,
    *,
    open_url: Optional[Callable[..., object]] = None,
) -> bool:
    transport = open_url or LOCAL_CDP_OPENER.open
    try:
        with transport(f"{endpoint}/json/version", timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    websocket_url = str(payload.get("webSocketDebuggerUrl", ""))
    endpoint_parts = urlparse(endpoint)
    websocket_parts = urlparse(websocket_url)
    return (
        websocket_parts.scheme == "ws"
        and websocket_parts.hostname in {"127.0.0.1", "localhost"}
        and websocket_parts.port == endpoint_parts.port
        and websocket_parts.path.startswith("/devtools/browser/")
    )


def wait_for_cdp_endpoint(
    endpoint: str,
    timeout_seconds: float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if is_cdp_endpoint_ready(endpoint):
            return
        sleep(0.25)
    raise FmkoreaBrowserStartupError(
        f"Chrome CDP endpoint did not become ready: {endpoint}"
    )


def validate_fmkorea_url(url: str, *, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in FMKOREA_ALLOWED_HOSTS:
        raise CrawlSourceError(
            f"FMKorea browser {label} left the allowed HTTPS origin: {url}"
        )
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise CrawlSourceError(f"FMKorea browser {label} is not a normal HTTPS URL: {url}")


class FmkoreaChromeSession:
    """Fetch FMKorea HTML through one dedicated, locally installed Chrome.

    Chrome is started directly with a dedicated persistent profile. Playwright
    attaches over a loopback-only CDP endpoint and reuses one page for all FM
    feeds. No browser headers, navigator values, personal cookies, or challenge
    responses are injected.
    """

    def __init__(
        self,
        config: FmkoreaBrowserConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        host_lock_factory: Callable[[], HostSessionLock] = HostSessionLock,
    ) -> None:
        self.config = config
        self._monotonic = monotonic
        self._sleep = sleep
        self._host_lock_factory = host_lock_factory
        self._last_navigation_completed_at: Optional[float] = None
        self.cleanup_warnings: list[str] = []
        self._host_lock: Optional[HostSessionLock] = None
        self._process: Optional[subprocess.Popen] = None
        self._playwright_manager = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright_timeout_error = None
        self._playwright_error = None

    @property
    def is_open(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    def __enter__(self) -> "FmkoreaChromeSession":
        # Opening is lazy so a D1 cooldown can skip both feeds without starting
        # Chrome or touching the source.
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def open(self, timeout_seconds: Optional[float] = None) -> None:
        if self.is_open:
            return
        if self._process is not None:
            raise FmkoreaBrowserStartupError(
                "FMKorea Chrome session is in a partially-open state."
            )

        try:
            from playwright.sync_api import (
                Error as PlaywrightError,
                TimeoutError as PlaywrightTimeoutError,
                sync_playwright,
            )
        except ImportError as exc:
            raise FmkoreaBrowserDependencyError(
                "Playwright is required for the FMKorea local browser runner. "
                "Install crawler/requirements-fmkorea-browser.txt."
            ) from exc

        startup_budget = self.config.startup_timeout_seconds
        if timeout_seconds is not None:
            if timeout_seconds <= 1.0:
                raise FmkoreaBrowserStartupError(
                    "No timeout budget remains for FMKorea Chrome startup."
                )
            startup_budget = min(startup_budget, timeout_seconds)
        startup_started_at = self._monotonic()

        self._host_lock = self._host_lock_factory()
        self._host_lock.acquire()
        try:
            profile_dir = prepare_dedicated_profile(self.config.profile_dir)
            assert_cdp_port_available(self.config.cdp_port)
        except Exception:
            self.close()
            raise
        args = [
            str(self.config.chrome_executable_path),
            f"--remote-debugging-port={self.config.cdp_port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
        ]
        if self.config.headless:
            args.append("--headless=new")
        args.append("about:blank")

        self._playwright_timeout_error = PlaywrightTimeoutError
        self._playwright_error = PlaywrightError
        self._playwright_manager = sync_playwright()
        try:
            self._playwright = self._playwright_manager.start()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            wait_for_cdp_endpoint(
                self.config.cdp_endpoint,
                startup_budget,
                monotonic=self._monotonic,
                sleep=self._sleep,
            )
            remaining_startup_budget = startup_budget - (
                self._monotonic() - startup_started_at
            )
            if remaining_startup_budget <= 1.0:
                raise FmkoreaBrowserStartupError(
                    "Chrome started but no timeout budget remained for CDP attach."
                )
            self._browser = self._playwright.chromium.connect_over_cdp(
                self.config.cdp_endpoint,
                timeout=int(remaining_startup_budget * 1000),
            )
            contexts = self._browser.contexts
            if not contexts:
                raise FmkoreaBrowserStartupError(
                    "Chrome exposed no persistent browser context over CDP."
                )
            self._context = contexts[0]
            pages = [page for page in self._context.pages if not page.is_closed()]
            self._page = next(
                (page for page in pages if page.url == "about:blank"),
                pages[0] if pages else None,
            )
            if self._page is None:
                self._page = self._context.new_page()
        except Exception as exc:
            self.close()
            if isinstance(exc, FmkoreaBrowserStartupError):
                raise
            raise FmkoreaBrowserStartupError(
                "Could not start or attach to the dedicated FMKorea Chrome: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def close(self) -> None:
        browser = self._browser
        playwright = self._playwright
        playwright_manager = self._playwright_manager
        process = self._process
        host_lock = self._host_lock
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._playwright_manager = None
        self._process = None
        self._host_lock = None

        if browser is not None:
            try:
                cdp_session = browser.new_browser_cdp_session()
                cdp_session.send("Browser.close")
                cdp_session.detach()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception as exc:
                self.cleanup_warnings.append(
                    f"Could not stop Playwright: {type(exc).__name__}: {exc}"
                )
        elif playwright_manager is not None:
            # ``sync_playwright().start()`` normally returns an object whose
            # ``stop`` method owns manager cleanup. If startup fails after the
            # manager created its transport but before returning that object,
            # give the context manager a best-effort chance to close the
            # partial driver and event loop as well.
            try:
                playwright_manager.__exit__(None, None, None)
            except Exception as exc:
                self.cleanup_warnings.append(
                    "Could not clean up partially-started Playwright: "
                    f"{type(exc).__name__}: {exc}"
                )
        if process is not None:
            self._stop_owned_process_tree(process)
            if not wait_for_cdp_listener_to_stop(
                self.config.cdp_port,
                monotonic=self._monotonic,
                sleep=self._sleep,
            ):
                self.cleanup_warnings.append(
                    "Chrome CDP listener remained active after cleanup "
                    f"(port={self.config.cdp_port})."
                )
        if host_lock is not None:
            try:
                host_lock.release()
            except Exception as exc:
                self.cleanup_warnings.append(
                    f"Could not release FMKorea host lock: {type(exc).__name__}: {exc}"
                )

    def _stop_owned_process_tree(self, process: subprocess.Popen) -> None:
        if os.name != "nt":
            self._stop_owned_posix_process_group(process)
            return

        try:
            if process.poll() is not None:
                return
            process.wait(timeout=3.0)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception as exc:
            self.cleanup_warnings.append(
                f"Could not inspect Chrome process: {type(exc).__name__}: {exc}"
            )
            return

        for force in (False, True):
            command = ["taskkill.exe", "/PID", str(process.pid), "/T"]
            if force:
                command.append("/F")
            try:
                subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                    check=False,
                )
                process.wait(timeout=3.0)
                return
            except subprocess.TimeoutExpired:
                continue
            except Exception as exc:
                self.cleanup_warnings.append(
                    "Could not stop the owned Chrome process tree: "
                    f"{type(exc).__name__}: {exc}"
                )
                break

        try:
            still_running = process.poll() is None
        except Exception:
            still_running = True
        if still_running:
            self.cleanup_warnings.append(
                f"Owned Chrome process tree may still be running (pid={process.pid})."
            )

    def _stop_owned_posix_process_group(self, process: subprocess.Popen) -> None:
        process_group_id = int(process.pid)
        try:
            if process.poll() is None:
                process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            pass
        except Exception as exc:
            self.cleanup_warnings.append(
                f"Could not inspect Chrome process: {type(exc).__name__}: {exc}"
            )

        if not _posix_process_group_exists(process_group_id):
            return
        for stop_signal in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process_group_id, stop_signal)
            except ProcessLookupError:
                return
            except Exception as exc:
                self.cleanup_warnings.append(
                    "Could not stop the owned Chrome process group: "
                    f"{type(exc).__name__}: {exc}"
                )
                return

            deadline = self._monotonic() + 3.0
            while self._monotonic() < deadline:
                if not _posix_process_group_exists(process_group_id):
                    return
                self._sleep(0.1)
        if _posix_process_group_exists(process_group_id):
            self.cleanup_warnings.append(
                "Owned Chrome process group may still be running "
                f"(pgid={process_group_id})."
            )

    def __call__(self, url: str, timeout_seconds: float) -> str:
        validate_fmkorea_url(url, label="request")
        call_started_at = self._monotonic()
        self.open(timeout_seconds=timeout_seconds)
        assert self._page is not None
        assert self._playwright_timeout_error is not None
        assert self._playwright_error is not None
        if self._last_navigation_completed_at is not None:
            elapsed = call_started_at - self._last_navigation_completed_at
            delay = max(
                0.0,
                self.config.min_navigation_interval_seconds - elapsed,
            )
            if delay >= timeout_seconds - 1.0:
                raise CrawlTimeoutError(
                    "FMKorea origin-wide request interval exhausted the current "
                    "navigation timeout budget."
                )
            if delay > 0:
                self._sleep(delay)
        remaining_seconds = timeout_seconds - (self._monotonic() - call_started_at)
        if remaining_seconds <= 1.0:
            raise CrawlTimeoutError(
                "FMKorea browser has no navigation timeout budget remaining."
            )
        timeout_ms = max(1_000, int(remaining_seconds * 1000))

        try:
            response = self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            if response is None:
                raise CrawlSourceError(
                    f"Chrome returned no document response while requesting {url}."
                )
            status = int(response.status)
            final_url = self._page.url
            try:
                validate_fmkorea_url(final_url, label="redirect")
            except CrawlSourceError as exc:
                raise CrawlBlockedError(
                    "FMKorea redirected the browser outside its allowed HTTPS origin: "
                    f"{final_url}"
                ) from exc
            if status in {403, 429, 430}:
                raise CrawlBlockedError(
                    f"Blocked by FMKorea with HTTP {status} while requesting {url}."
                )
            if status == 408 or 500 <= status <= 599:
                raise CrawlTransientError(
                    f"FMKorea returned transient HTTP {status} while requesting {url}."
                )
            if status >= 400:
                raise CrawlSourceError(
                    f"FMKorea returned HTTP {status} while requesting {url}."
                )

            content_type = (response.header_value("content-type") or "").lower()
            if not (
                content_type.startswith("text/html")
                or content_type.startswith("application/xhtml+xml")
            ):
                raise CrawlSourceError(
                    "FMKorea browser response was not HTML "
                    f"(content-type={content_type or '<missing>'})."
                )
            html = response.text()
        except (CrawlBlockedError, CrawlSourceError):
            raise
        except self._playwright_timeout_error as exc:
            raise CrawlTimeoutError(
                f"Chrome timed out while requesting {url}."
            ) from exc
        except self._playwright_error as exc:
            raise CrawlTransientError(
                f"Chrome navigation failed while requesting {url}: {exc}"
            ) from exc
        finally:
            self._last_navigation_completed_at = self._monotonic()

        blocked_reason = detect_blocked_html(html)
        if blocked_reason:
            raise CrawlBlockedError(
                f"Blocked by FMKorea: detected {blocked_reason} while requesting {url}."
            )
        return html
