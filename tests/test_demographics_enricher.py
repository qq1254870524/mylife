from __future__ import annotations

import unittest
from pathlib import Path

from demographics_enricher import enrich_demographics, zodiac_from_birthday
from models import PersonInput, ProfileResult


class DemographicsEnricherTests(unittest.TestCase):
    def test_zodiac_is_determined_from_complete_birthday(self) -> None:
        self.assertEqual(zodiac_from_birthday("10/03/1977"), "Libra (September 23 - October 22)")
        self.assertEqual(zodiac_from_birthday("01/02/1980"), "Capricorn (December 22 - January 19)")

    def test_strict_duplicate_profile_can_supply_missing_gender(self) -> None:
        person = PersonInput(Path("people.csv"), 2, ["name"], {"name": "Jane Doe"}, "Jane Doe", "Jane", "", "Doe")
        selected = ProfileResult(
            1,
            "https://www.mylife.com/jane-doe/e1",
            full_name="Jane Doe",
            age="42",
            birthday="03/07/1984",
            profile_summary="Phone (303) 555-1234",
        )
        donor = ProfileResult(
            2,
            "https://www.mylife.com/jane-doe/e2",
            full_name="Jane A Doe",
            age="42",
            gender="Female",
            profile_summary="Phone 303-555-1234",
        )
        enrich_demographics(person, selected, [selected, donor])
        self.assertEqual(selected.gender, "Female")
        self.assertEqual(selected.zodiac, "Pisces (February 19 - March 20)")
        self.assertIn("同身份重复档案", selected.demographics_note)


if __name__ == "__main__":
    unittest.main()
