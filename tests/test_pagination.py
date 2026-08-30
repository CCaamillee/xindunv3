from __future__ import annotations

import math
import unittest

from services.workbook_data import get_encounter_dataframe


class EncounterPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frame = get_encounter_dataframe()

    def test_complete_workbook_encounters_can_be_paginated(self) -> None:
        page_size = 50
        self.assertEqual(len(self.frame), 14419)
        self.assertEqual(math.ceil(len(self.frame) / page_size), 289)
        self.assertEqual(len(self.frame.iloc[:page_size]), page_size)

    def test_pages_do_not_overlap(self) -> None:
        first = set(self.frame.iloc[:50]["encounter_key"])
        second = set(self.frame.iloc[50:100]["encounter_key"])
        self.assertFalse(first & second)

    def test_encounter_keys_are_stable_and_unique(self) -> None:
        self.assertTrue(self.frame["encounter_key"].is_unique)
        expected = self.frame["regno"].astype(str) + "::" + self.frame["admno"].astype(str)
        self.assertTrue(expected.equals(self.frame["encounter_key"]))


if __name__ == "__main__":
    unittest.main()
