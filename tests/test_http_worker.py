from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

import requests

from browser_worker import Cancelled
from http_worker import HttpInterfaceSession
from mylife_parser import build_search_url
from proxy_pool import ProxyGeo, ProxySpec


SEARCH_HTML = """
<html><head><title>People Search</title></head><body>
  <div class="search-result">
    <a href="/john-smith/e123456">John Smith, 50</a>
    <span>Age 50 Lives in Austin, TX</span>
  </div>
</body></html>
"""

DETAIL_HTML = """
<html><head><title>John Smith, 50 - Austin, TX - Reputation &amp; Contact Details</title></head>
<body><h1>John Smith, 50</h1>
  <div>Currently lives in Austin, TX. Birthday: January 2, 1976. Gender: Male.</div>
</body></html>
"""

BLOCKED_HTML = """
<html><head><title>Just a moment...</title></head>
<body><div id="challenge-platform">cf-chl-test</div></body></html>
"""


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.html = ""

    def evaluate(self, _script: str) -> str:
        return "FixtureBrowser/151"

    def content(self) -> str:
        return self.html

    def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url


class FakeContext:
    def cookies(self) -> list[dict[str, str]]:
        return [{"name": "cf_clearance", "value": "fixture", "domain": ".mylife.com", "path": "/"}]


class FakeResponse:
    def __init__(self, status_code: int, url: str, text: str) -> None:
        self.status_code = status_code
        self.url = url
        self.text = text


class QueuedSession(requests.Session):
    def __init__(self, responses: list[FakeResponse | requests.RequestException] | None = None) -> None:
        super().__init__()
        self.responses = list(responses or [])
        self.get_calls: list[str] = []
        self.closed = False

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.get_calls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected HTTP GET: {url}")
        response = self.responses.pop(0)
        if isinstance(response, requests.RequestException):
            raise response
        return response

    def close(self) -> None:
        self.closed = True
        super().close()


class HttpWorkerTests(unittest.TestCase):
    def _worker(
        self,
        directory: str,
        sessions: list[QueuedSession],
        *,
        proxy: bool = False,
    ) -> HttpInterfaceSession:
        stop_event = threading.Event()

        def factory() -> QueuedSession:
            if not sessions:
                raise AssertionError("unexpected session creation")
            return sessions.pop(0)

        proxy_spec = ProxySpec(1, "127.0.0.1", 1080, "user", "pass", "https://refresh.invalid") if proxy else None
        worker = HttpInterfaceSession(
            worker_number=1,
            profile_dir=Path(directory) / "profile",
            mode="无头",
            proxy_spec=proxy_spec,
            proxy_geo=ProxyGeo(),
            stop_event=stop_event,
            log=lambda _message: None,
            max_search_pages=3,
            session_factory=factory,
        )
        worker.page = FakePage()
        worker.context = FakeContext()

        def navigate(url: str) -> None:
            worker.page.url = url
            worker.page.html = DETAIL_HTML if "/e123456" in url else SEARCH_HTML

        worker._navigate = navigate  # type: ignore[method-assign]
        return worker

    def test_browser_bootstrap_then_http_detail_reuses_cookie_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detail_url = "https://www.mylife.com/john-smith/e123456"
            http_session = QueuedSession([FakeResponse(200, detail_url, DETAIL_HTML)])
            worker = self._worker(directory, [http_session], proxy=True)
            search_url = build_search_url("John", "Smith")

            results = worker._collect_search_results(search_url)
            self.assertEqual(len(results), 1)
            self.assertEqual(http_session.get_calls, [], "首个搜索页应复用浏览器初始化文档")
            self.assertEqual(http_session.cookies.get("cf_clearance"), "fixture")
            self.assertEqual(http_session.headers["User-Agent"], "FixtureBrowser/151")
            self.assertIn("socks5h://", http_session.proxies["https"])

            detail = worker._collect_profile(results[0], 1, "姓名", search_url)
            self.assertEqual(detail.full_name, "John Smith")
            self.assertEqual(detail.birthday, "January 2, 1976")
            self.assertEqual(http_session.get_calls, [detail_url])
            worker.close()

    def test_http_403_resyncs_browser_cookie_and_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detail_url = "https://www.mylife.com/john-smith/e123456"
            expired = QueuedSession([FakeResponse(403, detail_url, BLOCKED_HTML)])
            renewed = QueuedSession([FakeResponse(200, detail_url, DETAIL_HTML)])
            worker = self._worker(directory, [expired, renewed])
            search_url = build_search_url("John", "Smith")
            result = worker._collect_search_results(search_url)[0]

            detail = worker._collect_profile(result, 1, "姓名", search_url)
            self.assertEqual(detail.birthday, "January 2, 1976")
            self.assertTrue(expired.closed)
            self.assertEqual(len(expired.get_calls), 1)
            self.assertEqual(len(renewed.get_calls), 1)
            worker.close()

    def test_http_connection_error_uses_verified_browser_document_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            search_url = build_search_url("John", "Smith")
            detail_url = "https://www.mylife.com/john-smith/e123456"
            first = QueuedSession([requests.ConnectionError("fixture disconnect")])
            second = QueuedSession([requests.ConnectionError("fixture disconnect")])
            worker = self._worker(directory, [first, second])
            result = worker._collect_search_results(search_url)[0]

            detail = worker._collect_profile(result, 1, "姓名", search_url)
            self.assertEqual(detail.birthday, "January 2, 1976")
            self.assertTrue(first.closed)
            self.assertEqual(first.get_calls, [detail_url])
            self.assertEqual(second.get_calls, [detail_url])
            worker.close()

    def test_stop_is_checked_before_http_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = self._worker(directory, [QueuedSession()])
            worker.stop_event.set()
            with self.assertRaises(Cancelled):
                worker._fetch_html(build_search_url("John", "Smith"))


if __name__ == "__main__":
    unittest.main()
