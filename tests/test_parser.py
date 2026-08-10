from __future__ import annotations

import unittest

from models import SearchResult
from mylife_parser import build_search_url, extract_age, extract_birthday, extract_gender, extract_zodiac, parse_profile_html, parse_search_results


class ParserTests(unittest.TestCase):
    def test_search_url_location_then_name(self) -> None:
        located = build_search_url("Baoyu", "Zhang", "Davie, FL 33314")
        fallback = build_search_url("Baoyu", "Zhang")
        self.assertIn("searchLocation=Davie%2C+FL+33314", located)
        self.assertIn("search=Baoyu+Zhang", fallback)

    def test_search_results_deduplicate_and_next(self) -> None:
        html = """
        <ul>
          <li class="search-result"><a href="/jane-doe/e1001"><h2>Jane Doe</h2></a><span>Age: 42</span><div>Lives in Denver, CO</div></li>
          <li class="search-result"><a href="https://www.mylife.com/john-doe/c2002">John Doe</a><span>Age 51</span></li>
          <li><a href="/jane-doe/e1001">duplicate</a></li>
        </ul>
        <a rel="next" href="?page=2">Next</a>
        """
        results, next_url, no_results = parse_search_results(html, "https://www.mylife.com/search?page=1")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].full_name, "Jane Doe")
        self.assertEqual(results[0].age, "42")
        self.assertEqual(next_url, "https://www.mylife.com/search?page=2")
        self.assertFalse(no_results)

    def test_profile_birthday(self) -> None:
        html = """
        <html><body><h1>Jane Doe Reputation Profile</h1>
        <section>Age: 42 Birthday: March 7, 1984 Location: Denver, CO</section>
        </body></html>
        """
        seed = SearchResult("https://www.mylife.com/jane-doe/e1001", full_name="Jane Doe")
        result = parse_profile_html(html, seed, 1, "姓名")
        self.assertEqual(result.full_name, "Jane Doe")
        self.assertEqual(result.age, "42")
        self.assertEqual(result.birthday, "March 7, 1984")
        self.assertEqual(extract_birthday("Date of Birth: 03/07/1984"), "03/07/1984")

    def test_profile_without_public_birthday_is_explicit(self) -> None:
        seed = SearchResult("https://www.mylife.com/jane-doe/e1001", full_name="Jane Doe")
        result = parse_profile_html("<h1>Jane Doe</h1><p>Date of Birth: Public Private</p>", seed, 1, "姓名")
        self.assertEqual(result.birthday, "")
        self.assertEqual(result.message, "详情页未公开生日")

    def test_comma_age_in_real_style_titles(self) -> None:
        html = '<li class="search-result"><a href="/olivia-rigor/e1">Olivia Rigor, 35</a><div>Jersey City, NJ 07306</div></li>'
        results, _, _ = parse_search_results(html, "https://www.mylife.com/search")
        self.assertEqual(results[0].full_name, "Olivia Rigor")
        self.assertEqual(results[0].age, "35")
        profile = parse_profile_html(
            "<h1>Olivia Rigor, 35</h1><p>Birthday: March 7, 1991</p>",
            results[0],
            1,
            "姓名",
        )
        self.assertEqual(profile.full_name, "Olivia Rigor")
        self.assertEqual(profile.age, "35")
        self.assertEqual(extract_age("Mary Dangerfield, 77 Houston, TX"), "77")

    def test_gender_from_profile_span(self) -> None:
        html = '<div><label>Gender</label><span class="fz-md roboto-reg"> Female </span></div>'
        self.assertEqual(extract_gender(html), "Female")
        seed = SearchResult("https://www.mylife.com/jane-doe/e1001", full_name="Jane Doe")
        result = parse_profile_html(f"<h1>Jane Doe, 42</h1>{html}", seed, 1, "姓名")
        self.assertEqual(result.gender, "Female")

    def test_real_sentence_age_and_birthday(self) -> None:
        text = "is 48 years old and was born on 10/03/1977. Currently, Jennifer lives in"
        self.assertEqual(extract_age(text), "48")
        self.assertEqual(extract_birthday(text), "10/03/1977")

    def test_zodiac_from_profile_font(self) -> None:
        html = '<font _mstmutation="1">Libra (September 23 - October 22)</font>'
        self.assertEqual(extract_zodiac(html), "Libra (September 23 - October 22)")
        seed = SearchResult("https://www.mylife.com/jane-doe/e1001", full_name="Jane Doe")
        result = parse_profile_html(f"<h1>Jane Doe, 48</h1>{html}", seed, 1, "姓名")
        self.assertEqual(result.zodiac, "Libra (September 23 - October 22)")


if __name__ == "__main__":
    unittest.main()
