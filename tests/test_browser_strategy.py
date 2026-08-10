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
        selected = session.process(self.person())[0]
        self.assertEqual(len(calls), 2)
        self.assertIn("searchLocation=", calls[0])
        self.assertIn("search=Jane+Doe", calls[1])
        self.assertEqual(selected.profile_url, hit.profile_url)
        self.assertIn("唯一结果直接确认", selected.query_strategy)

    def test_single_location_result_is_confirmed_even_when_age_differs(self) -> None:
        wrong_age = SearchResult("https://www.mylife.com/jane-doe/e1", "Jane Doe", "71", "Denver CO 80202")
        session, calls = self.session([[wrong_age]])
        selected = session.process(self.person())[0]
        self.assertEqual(len(calls), 1)
        self.assertEqual(selected.profile_url, wrong_age.profile_url)
        self.assertIn("唯一结果直接确认", selected.query_strategy)
        self.assertIn("搜索结果唯一", selected.message)

    def test_historical_403_keeps_browser_when_one_result_page_is_already_usable(self) -> None:
        url = "https://www.mylife.com/pub-multisearch.pubview?searchFirstName=Aronde&searchLastName=Hogg"
        html = """
        <html><head><title>Get Reputation Report details, Phone, Address &amp; more.</title></head>
        <body><h1 class="search-result-heading">We Found 1 Result for Aronde Hogg</h1>
        <a href="/aronde-hogg/e123">Aronde Torrez Hogg</a><div>Jacksonville, FL, 32218-7372</div></body></html>
        """
        page = SimpleNamespace(
            url=url,
            goto=lambda *_args, **_kwargs: SimpleNamespace(status=403),
            title=lambda: "Get Reputation Report details, Phone, Address & more.",
            content=lambda: html,
        )
        session = object.__new__(BrowserSession)
        session.worker_number = 2
        session.page = page
        session.stop_event = threading.Event()
        session.challenge_failures = 0
        session._captured_success = True
        session._pause = lambda *_args: None
        session._handle_cloudflare = lambda: None
        session._apply_profile_geolocation = lambda: None
        session._body_text = lambda: ""
        session._human_scroll = lambda: None
        logged: list[str] = []
        session.log = logged.append

        session._navigate(url)

        self.assertEqual(session.challenge_failures, 0)
        self.assertTrue(any("已渲染有效 MyLife 页面" in line for line in logged))

    def test_clean_former_name_is_used_after_primary_name_has_no_age_match(self) -> None:
        person = self.person()
        person.original = {"former_names": "Jane Smith"}
        wrong_age = SearchResult("https://www.mylife.com/jane-doe/e1", "Jane Doe", "71", "Denver CO 80202")
        wrong_age_2 = SearchResult("https://www.mylife.com/jane-doe/e3", "Jane Doe", "70", "Denver CO 80202")
        exact_alias = SearchResult("https://www.mylife.com/jane-smith/e2", "Jane Smith", "42", "Denver CO 80202")
        session, calls = self.session([[wrong_age, wrong_age_2], [], [exact_alias]])
        selected = session.process(person)[0]
        self.assertEqual(len(calls), 3)
        self.assertIn("searchFirstName=Jane", calls[2])
        self.assertIn("searchLastName=Smith", calls[2])
        self.assertEqual(selected.profile_url, exact_alias.profile_url)
        self.assertTrue(selected.query_strategy.startswith("曾用名(Jane Smith)+城市州邮编"))

    def test_past_location_search_runs_before_alias_when_current_and_name_have_no_age_match(self) -> None:
        person = self.person()
        person.original = {"past_addresses": "82 Deborah Ct, Plainfield, NJ 07062"}
        wrong_age = SearchResult("https://www.mylife.com/jane-doe/e1", "Jane Doe", "71", "Denver CO 80202")
        wrong_age_2 = SearchResult("https://www.mylife.com/jane-doe/e3", "Jane Doe", "70", "Denver CO 80202")
        exact_past = SearchResult("https://www.mylife.com/jane-doe/e2", "Jane Doe", "42", "Plainfield NJ 07062")
        session, calls = self.session([[wrong_age, wrong_age_2], [], [exact_past]])
        selected = session.process(person)[0]
        self.assertEqual(len(calls), 3)
        self.assertIn("searchLocation=Plainfield%2C+NJ+07062", calls[2])
        self.assertTrue(selected.query_strategy.startswith("姓名+曾用城市州邮编"))

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
