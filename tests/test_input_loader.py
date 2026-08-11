from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import openpyxl

from input_loader import load_people


class InputLoaderTests(unittest.TestCase):
    def test_headerless_people_xlsx_is_inferred_and_keeps_first_physical_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headerless.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(
                [
                    "3035550100",
                    "111-22-3333",
                    "1984-06-12",
                    "Jane Doe",
                    "42",
                    "1450 Pearl St",
                    "Denver",
                    "CO",
                    80202,
                    "jane@example.com",
                    "(303) 555-0100",
                    1984,
                    42,
                ]
            )
            sheet.append(
                [
                    "2145550199",
                    "222-33-4444",
                    "1975-01-02",
                    "John Smith",
                    "51",
                    "200 Main St",
                    "Dallas",
                    "TX",
                    7501,
                    "john@example.com",
                    "(214) 555-0199",
                    1975,
                    51,
                ]
            )
            workbook.save(path)
            workbook.close()

            headers, people = load_people(path)
            self.assertEqual(headers[:9], [
                "primary_phone", "ssn", "known_birthday", "full_name", "age",
                "current_address", "city", "state", "zip_code",
            ])
            self.assertEqual(len(people), 2)
            self.assertEqual(people[0].source_row, 1)
            self.assertEqual(people[0].full_name, "Jane Doe")
            self.assertEqual(people[0].location, "Denver, CO 80202")
            self.assertEqual(people[0].age, "42")
            self.assertFalse(people[0].validation_error)
            self.assertEqual(people[1].zip_code, "07501")

    def test_only_full_row_duplicates_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same_first.csv"
            path.write_text(
                "query,full_name,note\n"
                "SAME,Jane Doe,资料A\n"
                "SAME,Jane Doe,资料B\n"
                "SAME,Jane Doe,资料A\n",
                encoding="utf-8-sig",
            )
            _headers, people = load_people(path)
            self.assertEqual(len(people), 2)
            self.assertEqual([person.original["note"] for person in people], ["资料A", "资料B"])
            self.assertEqual([person.source_row for person in people], [2, 3])

            _headers, physical_rows = load_people(path, deduplicate=False)
            self.assertEqual(len(physical_rows), 3)

    def test_csv_arbitrary_order_and_extra_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["备注", "邮编", "姓", "城市", "名", "州"])
                writer.writerow(["keep", "33314", "Zhang", "Davie", "Baoyu", "fl"])
            headers, people = load_people(path)
            self.assertEqual(headers[0], "备注")
            self.assertEqual(len(people), 1)
            person = people[0]
            self.assertEqual(person.full_name, "Baoyu Zhang")
            self.assertEqual(person.location, "Davie, FL 33314")
            self.assertEqual(person.original["备注"], "keep")

    def test_xlsx_full_name_and_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(["姓名", "地址", "附加"])
            sheet.append(["Baoyu Zhang", "Davie, FL 33314", "x"])
            workbook.save(path)
            workbook.close()
            _, people = load_people(path)
            self.assertEqual(people[0].first_name, "Baoyu")
            self.assertEqual(people[0].last_name, "Zhang")
            self.assertEqual(people[0].city, "Davie")
            self.assertEqual(people[0].state, "FL")
            self.assertEqual(people[0].zip_code, "33314")

    def test_email_without_name_is_invalid_not_misclassified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.txt"
            path.write_text("name@example.com\n", encoding="utf-8")
            _, people = load_people(path)
            self.assertTrue(people[0].validation_error)

    def test_age_column_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.csv"
            path.write_text("first_name,last_name,age\nJane,Doe,42.0\n", encoding="utf-8")
            _, people = load_people(path)
            self.assertEqual(people[0].age, "42")

    def test_current_address_supplies_zip_when_city_state_column_has_no_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.csv"
            path.write_text(
                "first_name,last_name,address,current_address\n"
                "Jane,Doe,Denver CO,1450 Pearl St Denver CO 80202\n",
                encoding="utf-8",
            )
            _, people = load_people(path)
            self.assertEqual(people[0].city, "Denver")
            self.assertEqual(people[0].state, "CO")
            self.assertEqual(people[0].zip_code, "80202")


if __name__ == "__main__":
    unittest.main()
