import pandas as pd

import batch_booking_safety as safety
from booking_eligibility import can_create_order, can_process_row, create_block_reason


BASE = {'姓名': '王小明', '電話': '0912345678', '地址': '台北市大安區仁愛路一段1號',
        '日期': '2027/01/20', '開始時間': '09:00', '結束時間': '12:00', '服務人時': '定期清潔',
        '狀態': '未安排', '訂單編號': '', '預約條件': ''}


def test_shared_rule_requires_blank_order_unarranged_and_no_hold():
    assert can_create_order(BASE)
    assert can_create_order({**BASE, '預約條件': '可預約：2026大掃除 V 欄已確認'})
    assert not can_create_order({**BASE, '預約條件': '先不約：2026大掃除 V 欄為 FALSE，尚未確認'})
    assert not can_create_order({**BASE, '狀態': '待確認'})
    assert not can_create_order({**BASE, '訂單編號': 'LC1'})
    assert can_create_order({k: v for k, v in BASE.items() if k != '預約條件'})


def test_rows_with_order_still_processed_for_mail_and_calendar():
    assert can_process_row({**BASE, '訂單編號': 'LC1', '預約條件': '先不約：x'})
    assert not can_process_row({**BASE, '預約條件': '先不約：x'})


def test_reason_is_shown_for_skipped_rows():
    assert create_block_reason({**BASE, '狀態': '暫停'}) == '狀態為「暫停」，非未安排'
    assert create_block_reason({**BASE, '預約條件': '先不約：排程 A 欄標記「改」'}).startswith('先不約')


def test_all_batch_runners_share_recover_filter(monkeypatch):
    rows = [{**BASE, '__sheet_row__': 2},
            {**BASE, '__sheet_row__': 3, '預約條件': '先不約：排程 A 欄標記「改」，留待最後成單'},
            {**BASE, '__sheet_row__': 4, '狀態': '待確認'}]
    monkeypatch.setattr(safety, '_configure_runtime', lambda env: None)
    monkeypatch.setattr(safety._orders, 'load_worksheet', lambda name: (object(), pd.DataFrame(rows)))
    monkeypatch.setattr(safety._orders, 'get_region_by_address', lambda address, accounts: '台北')
    monkeypatch.setattr(safety._orders, 'login', lambda session, email, password: True)
    monkeypatch.setattr(safety, '_row_key', lambda row: ('0912345678', 'addr', '2027-01-20', 'p', 'p'))
    monkeypatch.setattr(safety, '_fetch_phone_blocks', lambda session, phone: [])
    logs = []
    _, remaining, recovered, blocked, _ = safety._recover_before_create(
        'dev', '台北', 'e', 'p', '台北202701', 2, 4, None, ['建單'], logs.append)
    assert remaining == [2] and recovered == [] and blocked == []
    assert any('第 3 列不建單：先不約' in line for line in logs)
    assert any('第 4 列不建單：狀態為「待確認」' in line for line in logs)
