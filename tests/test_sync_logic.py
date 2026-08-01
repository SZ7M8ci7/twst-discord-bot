import unittest

from app.sync_logic import extract_furniture_name, parse_sync_command


class SyncLogicTests(unittest.TestCase):
    def test_rejects_empty_pending_command(self):
        self.assertIsNone(parse_sync_command("0:"))

    def test_rejects_empty_done_command(self):
        self.assertIsNone(parse_sync_command("1:   "))

    def test_parses_valid_commands(self):
        self.assertEqual(parse_sync_command("1:家具A"), (True, "家具ａ"))
        self.assertEqual(parse_sync_command("0:家具A"), (False, "家具ａ"))

    def test_extracts_exact_furniture_name(self):
        content = "家具名：家具A\n家具：椅子"
        self.assertEqual(extract_furniture_name(content), "家具ａ")

    def test_extracts_furniture_name_from_first_line(self):
        content = "くつろぎクッションソファ\n家具：椅子"
        self.assertEqual(
            extract_furniture_name(content),
            "くつろぎクッションソファ",
        )

    def test_short_name_does_not_match_longer_furniture_name(self):
        _, furniture_name = parse_sync_command("0:家具")
        self.assertNotEqual(
            extract_furniture_name("家具名：家具A"),
            furniture_name,
        )

    def test_does_not_extract_empty_text(self):
        self.assertIsNone(extract_furniture_name("  \n"))


if __name__ == "__main__":
    unittest.main()
