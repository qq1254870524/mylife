from __future__ import annotations

import unittest
from pathlib import Path

from models import PersonInput, SearchResult
from search_planner import former_name_pairs, has_exact_age, merge_search_results, past_locations


class SearchPlannerTests(unittest.TestCase):
    def person(self, former_names: str) -> PersonInput:
        return PersonInput(
            source_path=Path("people.csv"),
            source_row=2,
            headers=["full_name", "former_names"],
            original={"former_names": former_names},
            first_value="Kyasia Moyers",
            first_name="Kyasia",
            last_name="Moyers",
            age="28",
        )

    def test_former_name_filters_parser_noise_and_unrelated_people(self) -> None:
        aliases = former_name_pairs(
            self.person("Kyasia resided in Baltimore|Kyasia Smith|Karen Gallo|Long Beach")
        )
        self.assertEqual(aliases, [("Kyasia", "Smith")])

    def test_exact_age_and_profile_url_merge(self) -> None:
        first = SearchResult("https://www.mylife.com/a/e1", age="71")
        exact = SearchResult("https://www.mylife.com/a/e2", age="28")
        target = [first]
        self.assertEqual(merge_search_results(target, [first, exact]), 1)
        self.assertTrue(has_exact_age(target, "28"))

    def test_past_locations_are_unique_and_skip_current_location(self) -> None:
        person = self.person("")
        person.city = "Baltimore"
        person.state = "MD"
        person.zip_code = "21218"
        person.original = {
            "past_addresses": (
                "701 E 30th St, Baltimore, MD 21218|"
                "82 Deborah Ct, Plainfield, NJ 07062|"
                "620 Newark Ave #9, Jersey City, NJ 07306|"
                "99 Other St, Plainfield, NJ 07062"
            )
        }
        self.assertEqual(past_locations(person), ["Plainfield, NJ 07062", "Jersey City, NJ 07306"])


if __name__ == "__main__":
    unittest.main()
