from __future__ import annotations

import unittest

from localization import get_language, normalize_language, set_language, tr


class LocalizationTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_language("ko")

    def test_switches_between_korean_and_english_immediately(self) -> None:
        set_language("ko")
        self.assertEqual(tr("다운로드 중", "Downloading"), "다운로드 중")
        set_language("en")
        self.assertEqual(tr("다운로드 중", "Downloading"), "Downloading")
        self.assertEqual(get_language(), "en")

    def test_unknown_language_falls_back_to_korean(self) -> None:
        self.assertEqual(normalize_language("fr"), "ko")


if __name__ == "__main__":
    unittest.main()
