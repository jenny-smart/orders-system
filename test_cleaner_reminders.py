import unittest
from datetime import date

from cleaner_reminders import (
    build_cleaner_message,
    cleaner_profile_ids,
    extract_cleaner_line,
    extract_cleaner_names,
)


class CleanerReminderTests(unittest.TestCase):
    def test_extract_cleaner_names(self):
        lines = ["2026-07-26 (日)", "09:00 - 18:00", "張志豪(1) X 李佩蓉(4) X 親思遠(1)"]
        self.assertEqual(extract_cleaner_names(lines), ["張志豪", "李佩蓉", "親思遠"])

    def test_extract_profile_id_and_line(self):
        roster = """
        <table><tr><td>21, 25</td><td>蔡立娟<br>居家專員</td>
        <td><a href="/cleaner1/22/shift">詳細資料</a></td></tr></table>
        """
        detail = '<input id="line" name="line" value="https://chat.line.biz/owner/chat/user">'
        self.assertEqual(cleaner_profile_ids(roster, ["蔡立娟"]), {"蔡立娟": "22"})
        self.assertEqual(extract_cleaner_line(detail), "https://chat.line.biz/owner/chat/user")

    def test_builds_merged_next_day_message(self):
        jobs = [
            {"order_no": "LC002", "service_time": "14:00-17:00", "address": "台北市B路2號"},
            {"order_no": "LC001", "service_time": "09:00-12:00", "address": "台北市A路1號"},
        ]
        message = build_cleaner_message(
            "蔡立娟", "2026-07-24", jobs, reference_date=date(2026, 7, 23)
        )
        self.assertIn("提醒您明日有排班", message)
        self.assertIn("1. 09:00-12:00", message)
        self.assertIn("2. 14:00-17:00", message)
        self.assertIn("請回覆「收到」", message)


if __name__ == "__main__":
    unittest.main()
