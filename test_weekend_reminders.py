import unittest
from datetime import date
from unittest.mock import Mock, patch

from weekend_reminders import (
    TRACKING_HEADERS,
    _line_urls_from_html,
    _listed_service_date_time,
    _sms_service_date_time,
    _service_date_time,
    apply_line_reminder_statuses,
    build_reminder_message,
    line_id_from_chat_url,
    merge_tracking_rows,
    previous_workday,
    schedule_line_reminders,
    tracking_rows_tsv,
    upcoming_weekend,
    _tracking_worksheet,
)


class WeekendReminderTests(unittest.TestCase):
    def test_tracking_headers_accept_safe_older_subset(self):
        older = [
            "訂單編號", "服務日期", "服務時間", "姓名", "電話", "地址", "LINE",
            "LINE ID", "預約發送時間", "通知狀態", "通知時間",
            "回覆狀態", "回覆時間", "回覆備註", "最後更新",
        ]
        self.assertTrue(set(older).issubset(set(TRACKING_HEADERS)))

    @patch("weekend_reminders.orders.build_gsheet_client")
    def test_tracking_sheet_backs_up_unknown_columns_before_migration(self, build_client):
        old_values = [
            ["訂單編號", "服務日期", "人工備註"],
            ["LC001", "2026-08-01", "不可遺失"],
        ]
        worksheet = Mock()
        worksheet.row_values.return_value = old_values[0]
        worksheet.get_all_values.return_value = old_values
        worksheet.col_count = 3
        backup = Mock()
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = worksheet
        spreadsheet.add_worksheet.return_value = backup
        build_client.return_value.open_by_key.return_value = spreadsheet

        self.assertIs(_tracking_worksheet(), worksheet)

        backup_call = spreadsheet.add_worksheet.call_args
        self.assertIn("週末服務提醒_備份_", backup_call.kwargs["title"])
        backup.update.assert_called_once_with(range_name="A1", values=old_values)
        migrated = worksheet.update.call_args.kwargs["values"]
        self.assertEqual(migrated[0], TRACKING_HEADERS)
        self.assertEqual(migrated[1][TRACKING_HEADERS.index("訂單編號")], "LC001")
        self.assertEqual(migrated[1][TRACKING_HEADERS.index("服務日期")], "2026-08-01")

    def test_upcoming_weekend_from_weekday(self):
        self.assertEqual(
            upcoming_weekend(date(2026, 7, 23)),
            (date(2026, 7, 25), date(2026, 7, 26)),
        )

    def test_previous_workday_skips_holiday(self):
        self.assertEqual(
            previous_workday(date(2026, 9, 26), {date(2026, 9, 25)}),
            date(2026, 9, 24),
        )

    def test_message_asks_for_reply(self):
        message = build_reminder_message({
            "service_date": "2026-07-25",
            "service_time": "09:00-12:00",
            "address": "台北市中山區測試路1號",
        })
        self.assertIn("7/25（六） 09:00-12:00", message)
        self.assertIn("專員會在服務前5-10分鐘", message)
        self.assertIn("煩請留意聯繫訊息", message)
        self.assertIn("請點選下方「已收到」", message)

    def test_backend_card_and_line_id_parsing(self):
        lines = ["LC001", "2026-07-20 10:00:00", "居家清潔", "2026-07-25 (六)", "09:00 - 12:00"]
        self.assertEqual(_service_date_time(lines), ("2026-07-25", "09:00-12:00"))
        url = "https://chat.line.biz/U-owner/chat/U805b7af99c975eb040d1f82d7b1e8b6b"
        html = f'<table><tr><td>LC001</td><td><a href="{url}">LINE</a></td></tr></table>'
        self.assertEqual(_line_urls_from_html(html)["LC001"], url)
        self.assertEqual(line_id_from_chat_url(url), "U805b7af99c975eb040d1f82d7b1e8b6b")

    def test_sms_time_overrides_listed_service_time(self):
        lines = [
            "LC001", "2026-07-20 10:00:00", "居家清潔",
            "2026-07-25 (六)", "09:00 - 12:00",
            "簡訊時間：2026/07/26 14:30-17:30",
        ]
        self.assertEqual(
            _listed_service_date_time(lines), ("2026-07-25", "09:00-12:00")
        )
        self.assertEqual(
            _sms_service_date_time(lines, "2026-07-25"),
            ("2026-07-26", "14:30-17:30", True),
        )
        self.assertEqual(_service_date_time(lines), ("2026-07-26", "14:30-17:30"))

    def test_sms_short_date_uses_service_year(self):
        lines = [
            "LC001", "2026-07-20 10:00:00",
            "2026-07-25 (六)", "09:00 - 12:00",
            "簡訊通知時間", "7/26（日） 10:00～13:00",
        ]
        self.assertEqual(_service_date_time(lines), ("2026-07-26", "10:00-13:00"))

    def test_merge_adds_schedule_without_phone_matching(self):
        order_rows = [{
            "order_no": "LC001",
            "service_date": "2026-07-25",
            "service_time": "09:00-12:00",
            "name": "王小姐",
            "phone": "",
            "address": "",
            "line_url": "https://chat.line.biz/U-owner/chat/U-user",
            "message": "提醒",
        }]
        merged = merge_tracking_rows(order_rows, [], scheduled_at="2026-07-24 09:03")
        self.assertEqual(merged[0]["預約發送時間"], "2026-07-24 09:03")
        self.assertEqual(merged[0]["通知狀態"], "待通知")

    def test_applies_postback_status_and_builds_copy_text(self):
        rows = [{
            "訂單編號": "LC001",
            "服務日期": "2026-07-25",
            "通知狀態": "已排程",
            "回覆狀態": "未回覆",
        }]
        statuses = [{
            "reminder_key": "LC001|2026-07-25",
            "line_user_id": "U-user",
            "status": "replied",
            "scheduled_at": "2026-07-24T01:03:00.000Z",
            "sent_at": "2026-07-24T01:03:15.000Z",
            "replied_at": "2026-07-24T01:04:00.000Z",
            "last_error": None,
        }]
        synced = apply_line_reminder_statuses(rows, statuses)
        self.assertEqual(synced[0]["LINE ID"], "U-user")
        self.assertEqual(synced[0]["通知狀態"], "已通知")
        self.assertEqual(synced[0]["回覆狀態"], "已回覆")
        self.assertEqual(synced[0]["回覆時間"], "2026-07-24 09:04")
        self.assertIn("LINE ID", tracking_rows_tsv(synced).splitlines()[0])

    @patch("weekend_reminders.requests.post")
    def test_schedule_uses_line_id_from_chat_url_without_phone(self, post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "reminders": [{
                "reminder_key": "LC001|2026-07-25",
                "scheduled_at": "2026-07-24T01:03:00.000Z",
            }],
        }
        post.return_value = response
        saved, skipped = schedule_line_reminders([{
            "訂單編號": "LC001",
            "服務日期": "2026-07-25",
            "電話": "",
            "LINE": "https://chat.line.biz/U-owner/chat/U-user",
            "預約發送時間": "2026-07-24 09:03",
            "LINE訊息": "提醒",
        }], "https://worker.example", "secret")
        self.assertEqual(len(saved), 1)
        self.assertEqual(skipped, [])
        payload = post.call_args.kwargs["json"]["reminders"][0]
        self.assertEqual(payload["line_user_id"], "U-user")
        self.assertEqual(payload["scheduled_at"], "2026-07-24T09:03:00+08:00")
        self.assertNotIn("phone", payload)


if __name__ == "__main__":
    unittest.main()
