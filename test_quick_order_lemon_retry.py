import json
import unittest
from unittest.mock import patch
import quick_order as q

SLOT = '2026-09-26_09:00-12:00'
AVAILABLE = '<input name="date_list[]" value="' + SLOT + '">'


class LemonRetryTest(unittest.TestCase):
    def run_query(self, replies, enabled=True, pre=None):
        with patch.object(q.orders, 'get_all_sections_raw', side_effect=replies) as query, \
             patch.object(q, 'ensure_lemon_cleaner_shifts', return_value=pre or {'success': True, 'assigned': ['檸檬人1', '檸檬人2']}) as shift, \
             patch.object(q, '_get_booking_token_for_payway', return_value='new-token'):
            result = q._query_booking_slot_with_lemon_retry(object(), 'https://example.invalid', '信用卡', {'person': '2'}, 'old-token', SLOT, enabled)
            return result, query, shift

    def test_empty_then_available_refreshes_token_and_requests_two(self):
        result, query, shift = self.run_query(['[]', AVAILABLE])
        self.assertEqual(result[1], 'new-token')
        self.assertEqual(query.call_args.args[2], 'new-token')
        self.assertEqual(shift.call_args.kwargs['person_count'], '2')
        self.assertEqual(shift.call_args.kwargs['period_s'], '09:00-12:00')

    def test_checkbox_without_names_is_accepted_like_batch(self):
        _, query, shift = self.run_query([AVAILABLE])
        shift.assert_not_called()
        query.assert_called_once()

    def test_known_shortage_only_requests_missing_person(self):
        initial = json.dumps([{'date': '2026-09-26', 'section': '09:00-12:00', 'cleaner': ['王小姐']}])
        _, _, shift = self.run_query([initial, AVAILABLE])
        self.assertEqual(shift.call_args.kwargs['person_count'], '1')

    def test_disabled_never_adds_shifts(self):
        with patch.object(q.orders, 'get_all_sections_raw', return_value='[]'), \
             patch.object(q, 'ensure_lemon_cleaner_shifts') as shift:
            with self.assertRaisesRegex(Exception, '後台未回傳可預約時段'):
                q._query_booking_slot_with_lemon_retry(object(), '', 'ATM', {'person': '2'}, '', SLOT, False)
            shift.assert_not_called()

    def test_failed_retry_shows_actual_skip_reason(self):
        with self.assertRaisesRegex(Exception, '檸檬人1：上4衝突'):
            self.run_query(['[]', '[]'], pre={'success': False, 'message': '可用檸檬人不足', 'skipped': [{'name': '檸檬人1', 'reason': '上4衝突'}]})

    def test_quick_uses_batch_shift_implementation(self):
        with patch('lemon_shift_conflict_patch.install_patch'), \
             patch.object(q.orders, 'ensure_lemon_cleaner_shifts', return_value={'success': True}) as batch:
            q.ensure_lemon_cleaner_shifts('session', 'base', '2026-09-26', '09:00-12:00', '2')
            batch.assert_called_once_with('session', 'base', '2026-09-26', '09:00-12:00', '2')
