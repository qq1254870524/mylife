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

    def test_complete_input_birthday_fills_private_profile_and_zodiac(self) -> None:
        person = PersonInput(
            Path("people.xlsx"),
            1,
            ["known_birthday", "full_name"],
            {"known_birthday": "1940-06-10", "full_name": "Shirley Beckman"},
            "1940-06-10",
            "Shirley",
            "",
            "Beckman",
        )
        selected = ProfileResult(
            1,
            "https://www.mylife.com/shirley-beckman/e1",
            full_name="Shirley Beckman",
            gender="Female",
            status="已匹配身份但生日未公开（高置信度）",
        )

        enrich_demographics(person, selected, [selected])

        self.assertEqual(selected.birthday, "06/10/1940")
        self.assertEqual(selected.zodiac, "Gemini (May 21 - June 20)")
        self.assertIn("输入资料完整日期", selected.demographics_note)
        self.assertIn("输入资料补充", selected.status)

    def test_partial_input_birthday_is_not_used_as_unique_date(self) -> None:
        person = PersonInput(
            Path("people.xlsx"),
            2,
            ["known_birthday", "full_name"],
            {"known_birthday": "1987-00-00", "full_name": "Jane Doe"},
            "1987-00-00",
            "Jane",
            "",
            "Doe",
        )
        selected = ProfileResult(1, "https://www.mylife.com/jane-doe/e1", full_name="Jane Doe")

        enrich_demographics(person, selected, [selected])

        self.assertEqual(selected.birthday, "")
        self.assertEqual(selected.zodiac, "")


if __name__ == "__main__":
    unittest.main()
