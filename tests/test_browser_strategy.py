from __future__ import annotations

import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from browser_worker import BrowserSession
from models import PersonInput, ProfileResult, SearchResult


class BrowserStrategyTests(unittest.TestCase):
    def person(self) -> PersonInput:
        return PersonInput(
            source_path=Path("people.csv"),
            source_row=2,
            headers=["name", "age", "city", "state", "zip"],
            original={},
            first_value="Jane Doe",
            first_name="Jane",
            last_name="Doe",
            city="Denver",
            state="CO",
            zip_code="80202",
            age="42",
        )

    def session(self, search_batches: list[list[SearchResult]]) -> tuple[BrowserSession, list[str]]:
        session = object.__new__(BrowserSession)
        session.worker_number = 1
        session.page = SimpleNamespace(url="https://www.mylife.com/pub-multisearch.pubview")
        session.stop_event = threading.Event()
        calls: list[str] = []

        def collect(url: str) -> list[SearchResult]:
            calls.append(url)
            return search_batches[len(calls) - 1]

        session._collect_search_results = collect
        session._collect_profile = lambda item, index, strategy, search_url: ProfileResult(
            index,
            item.profile_url,
            full_name=item.full_name,
            age=item.age,
            birthday="10/03/1983",
            location=item.location,
            query_strategy=strategy,
        )
        session.log = lambda _message: None
        return session, calls

    def test_location_hit_does_not_run_name_only_search(self) -> None:
        hit = SearchResult("https://www.mylife.com/jane-doe/e1", "Jane Doe", "42", "Denver CO 80202")
        session, calls = self.session([[hit]])
        session.process(self.person())
        self.assertEqual(len(calls), 1)
        self.assertIn("searchLocation=Denver%2C+CO+80202", calls[0])

    def test_location_zero_results_then_runs_name_only_search(self) -> None:
        hit = SearchResult("https://www.mylife.com/jane-doe/e1", "Jane Doe", "42", "Denver CO 80202")
        session, calls = self.session([[], [hit]])
        session.process(self.person())
        self.assertEqual(len(calls), 2)
        self.assertIn("searchLocation=", calls[0])
        self.assertIn("search=Jane+Doe", calls[1])

    def test_return_to_list_uses_saved_get_url_without_history_back(self) -> None:
        session = object.__new__(BrowserSession)
        session.worker_number = 1
        session.page = SimpleNamespace(url="https://www.mylife.com/jane-doe/e1")
        navigated: list[str] = []
        logged: list[str] = []
        session._close_visible_overlay = lambda: True

        def navigate(url: str) -> None:
            navigated.append(url)
            session.page.url = url

        session._navigate = navigate
        session.capture_diagnostic = lambda *_args, **_kwargs: None
        session.log = logged.append
        search_url = "https://www.mylife.com/pub-multisearch.pubview?searchFirstName=Jane"
        session._return_to_search_list(search_url)
        self.assertEqual(navigated, [search_url])
        self.assertTrue(any("固定 GET" in line for line in logged))


if __name__ == "__main__":
    unittest.main()
