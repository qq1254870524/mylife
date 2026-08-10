from __future__ import annotations

import unittest

from cross_row_enricher import enrich_cross_rows


class CrossRowEnricherTests(unittest.TestCase):
    def test_same_name_and_full_address_fill_missing_demographics(self) -> None:
        original = {"full_name": "Jane Doe", "current_address": "1450 Pearl St, Denver, CO 80202"}
        records = [
            (1, original, {"birthday": "03/07/1984", "gender": "Female", "zodiac": "Pisces (February 19 - March 20)"}, "t1"),
            (2, dict(original), {"birthday": "", "gender": "", "zodiac": "", "status": "无结果", "message": "网页无结果"}, "t2"),
        ]
        updates = enrich_cross_rows(records)
        self.assertEqual(updates[2]["birthday"], "03/07/1984")
        self.assertEqual(updates[2]["gender"], "Female")
        self.assertEqual(updates[2]["zodiac"], "Pisces (February 19 - March 20)")
        self.assertIn("跨输入行强信号", updates[2]["status"])

    def test_conflicting_gender_is_not_filled(self) -> None:
        original = {"full_name": "Jane Doe", "email_addresses": "jane@example.com"}
        records = [
            (1, original, {"gender": "Female"}, "t1"),
            (2, dict(original), {"gender": "Male"}, "t2"),
            (3, dict(original), {"gender": ""}, "t3"),
        ]
        self.assertNotIn(3, enrich_cross_rows(records))

    def test_same_address_with_different_name_does_not_merge(self) -> None:
        records = [
            (1, {"full_name": "Jane Doe", "current_address": "1450 Pearl St, Denver, CO 80202"}, {"gender": "Female"}, "t1"),
            (2, {"full_name": "John Doe", "current_address": "1450 Pearl St, Denver, CO 80202"}, {"gender": ""}, "t2"),
        ]
        self.assertEqual(enrich_cross_rows(records), {})


if __name__ == "__main__":
    unittest.main()
