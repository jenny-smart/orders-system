import unittest
from datetime import date

from weekend_reminders import (
    _line_urls_from_html,
    _service_date_time,
    build_reminder_message,
    merge_tracking_rows,
    previous_workday,
    upcoming_weekend,
)


class WeekendReminderTests(unittest.TestCase):
    def test_upcoming_weekend_from_weekday(self):
        self.assertEqual(upcoming_weekend(date(2026, 7, 23)), (date(2026, 7, 25), date(2026, 7, 26)))

    def test_previous_workday_skips_holiday(self):
        self.assertEqual(previous_workday(date(2026, 9, 26), {date(2026, 9, 25)}), date(2026, 9, 24))

    def test_message_asks_for_reply(self):
        message = build_reminder_message({
            "service_date": "2026-07-25", "service_time": "09:00-12:00", "address": "台北市中山區測試路1號",
        })
        self.assertIn("7/25（六） 09:00-12:00", message)
        self.assertIn("請回覆「收到」", message)

    def test_backend_card_parsing(self):
        lines = ["LC001", "2026-07-20 10:00:00", "居家清潔", "2026-07-25 (六)", "09:00 - 12:00"]
        self.assertEqual(_service_date_time(lines), ("2026-07-25", "09:00-12:00"))
        html = '<table><tr><td>LC001</td><td><a href="https://chat.line.biz/example">LINE</a></td></tr></table>'
        self.assertEqual(_line_urls_from_html(html)["LC001"], "https://chat.line.biz/example")

    def test_merge_preserves_tracking_status(self):
        orders = [{
            "order_no": "LC001", "service_date": "2026-07-25", "service_time": "09:00-12:00",
            "name": "王小姐", "phone": "0912345678", "address": "台北市中山區測試路1號",
            "line_url": "https://chat.line.biz/x", "message": "提醒",
        }]
        existing = [{"訂單編號": "LC001", "通知狀態": "已通知", "通知時間": "2026-07-24 10:00", "回覆狀態": "已回覆"}]
        merged = merge_tracking_rows(orders, existing)
        self.assertEqual(merged[0]["通知狀態"], "已通知")
        self.assertEqual(merged[0]["回覆狀態"], "已回覆")


if __name__ == "__main__":
    unittest.main()
