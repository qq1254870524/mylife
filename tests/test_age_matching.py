from __future__ import annotations

import unittest
from pathlib import Path

from browser_worker import select_best_birthday
from identity_matcher import score_candidate
from models import PersonInput, ProfileResult


class AgeMatchingTests(unittest.TestCase):
    def person(self, age: str = "42") -> PersonInput:
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
            age=age,
        )

    def test_exact_age_then_name_address_selects_one_birthday(self) -> None:
        details = [
            ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Jane Doe", age="42", birthday="January 1, 1984", location="Miami, FL"),
            ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane A Doe", age="42", birthday="March 7, 1984", location="Denver, CO 80202"),
            ProfileResult(3, "https://www.mylife.com/c/e3", full_name="Jane Doe", age="41", birthday="April 8, 1985", location="Denver, CO"),
        ]
        selected = select_best_birthday(self.person(), details, len(details))
        self.assertEqual(selected.birthday, "March 7, 1984")
        self.assertIn("已匹配生日", selected.status)
        self.assertIn("2 个同龄候选", selected.message)

    def test_no_age_match_falls_back_to_name_address(self) -> None:
        details = [
            ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Someone Else", age="41", birthday="January 1, 1985", location="Miami, FL"),
            ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane Doe", age="40", birthday="March 7, 1986", location="Denver, CO 80202"),
        ]
        selected = select_best_birthday(self.person(), details, len(details))
        self.assertEqual(selected.birthday, "March 7, 1986")
        self.assertIn("没有年龄 42", selected.message)

    def test_exact_phone_is_strongest_within_same_age_pool(self) -> None:
        person = self.person()
        person.original = {
            "primary_phone": "(303) 555-1234",
            "current_address": "1450 Pearl St, Denver, CO 80202",
        }
        details = [
            ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Jane Doe", age="42", birthday="January 1, 1984", location="Denver, CO 80202"),
            ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane Doe", age="42", birthday="March 7, 1984", location="Boulder, CO", profile_summary="Phone (303) 555-1234"),
        ]
        selected = select_best_birthday(person, details, len(details))
        self.assertEqual(selected.birthday, "March 7, 1984")
        self.assertIn("完整手机号一致", selected.message)

    def test_area_code_alone_is_weak_and_cannot_beat_past_address(self) -> None:
        person = self.person(age="")
        person.original = {
            "primary_phone": "(303) 555-1234",
            "past_addresses": "907 Pine Rd, Aurora, CO 80012",
        }
        details = [
            ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Jane Doe", birthday="January 1, 1984", profile_summary="Phone (303) 777-8888"),
            ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane Doe", birthday="March 7, 1984", profile_summary="Past address 907 Pine Rd, Aurora, CO 80012"),
        ]
        selected = select_best_birthday(person, details, len(details))
        self.assertEqual(selected.birthday, "March 7, 1984")
        self.assertIn("曾用地址", selected.message)

    def test_first_two_house_digits_require_same_street(self) -> None:
        person = self.person(age="")
        person.original = {"current_address": "1450 Pearl St, Denver, CO 80202"}
        good = ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Jane Doe", profile_summary="Current address 1499 Pearl St, Denver, CO")
        unrelated = ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane Doe", profile_summary="Current address 1488 Oak St, Denver, CO")
        good_score = score_candidate(person, good)
        unrelated_score = score_candidate(person, unrelated)
        self.assertGreater(good_score.score, unrelated_score.score)
        self.assertIn("当前地址门牌号前2位和街道一致", good_score.evidence)

    def test_birthday_presence_does_not_override_stronger_identity(self) -> None:
        person = self.person(age="")
        person.original = {"primary_phone": "(303) 555-1234"}
        details = [
            ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Jane Doe", profile_summary="Phone (303) 555-1234"),
            ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane Doe", birthday="March 7, 1984", location="Miami, FL"),
        ]
        selected = select_best_birthday(person, details, len(details))
        self.assertEqual(selected.profile_url, "https://www.mylife.com/a/e1")
        self.assertEqual(selected.birthday, "")

    def test_email_and_multiple_relatives_are_independent_evidence(self) -> None:
        person = self.person(age="")
        person.original = {
            "email_addresses": "jane@example.com",
            "possible_relatives": "John Doe|Mary Doe",
        }
        detail = ProfileResult(
            1,
            "https://www.mylife.com/a/e1",
            full_name="Jane Doe",
            profile_summary="Emails jane@example.com Possible relatives John Doe and Mary Doe",
        )
        scored = score_candidate(person, detail)
        self.assertIn("完整邮箱一致", scored.evidence)
        self.assertIn("共同亲属一致2人", scored.evidence)
        self.assertGreaterEqual(scored.strong_categories, 3)

    def test_birthday_derived_age_is_used_when_page_age_is_blank(self) -> None:
        details = [
            ProfileResult(1, "https://www.mylife.com/a/e1", full_name="Jane Doe", age="41", birthday="January 1, 1985", location="Denver, CO 80202"),
            ProfileResult(2, "https://www.mylife.com/b/e2", full_name="Jane Doe", age="", birthday="January 1, 1984", location="Denver, CO 80202"),
        ]
        selected = select_best_birthday(self.person(), details, len(details))
        self.assertEqual(selected.profile_url, "https://www.mylife.com/b/e2")
        self.assertIn("1 个同龄候选", selected.message)


if __name__ == "__main__":
    unittest.main()
