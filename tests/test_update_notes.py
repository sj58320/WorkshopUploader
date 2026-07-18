import unittest

from update_notes import normalize_update_note


class UpdateNoteTests(unittest.TestCase):
    def test_blank_note_uses_default(self):
        self.assertEqual(normalize_update_note(" \n\t "), "Update asset")

    def test_multiline_note_preserves_internal_lines(self):
        self.assertEqual(
            normalize_update_note("\n add models\n\nfix materials \n"),
            "add models\n\nfix materials",
        )


if __name__ == "__main__":
    unittest.main()
