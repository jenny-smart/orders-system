from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


orders_path = Path("orders.py")
s = orders_path.read_text(encoding="utf-8")

# 1) 同一人判斷：電話優先，無電話才退回姓名。
needle = '''    def _event_phone_match(order_phone, event):
        phone_norm = normalize_phone(order_phone) if order_phone else ""
        return bool(phone_norm) and phone_norm in _event_blob(event)

    def _event_addr_core_match(order_address, event):
'''
replacement = '''    def _event_phone_match(order_phone, event):
        phone_norm = normalize_phone(order_phone) if order_phone else ""
        return bool(phone_norm) and phone_norm in _event_blob(event)

    def _event_person_match(order, event):
        phone_norm = normalize_phone(order.get("phone", "")) if order.get("phone") else ""
        blob = _event_blob(event)
        if phone_norm:
            return phone_norm in blob
        name_norm = normalize_text_for_parse(order.get("name", ""))
        return bool(name_norm) and name_norm in blob

    def _event_addr_core_match(order_address, event):
'''
s = replace_once(s, needle, replacement, "add person matcher")

# 2) 新增後台同日同時段多筆異常。
needle = '''    matched_event_ids = set()
    result = {"backend_missing_in_calendar": [], "calendar_missing_in_backend": []}

    # ---------- 方向一：後台有、日曆沒有 ----------
'''
replacement = '''    matched_event_ids = set()
    result = {
        "backend_duplicate_slots": [],
        "backend_missing_in_calendar": [],
        "calendar_missing_in_backend": [],
    }

    # 同一區域、同一天、同一時段，後台有 2 筆以上就列異常；
    # 不論姓名、電話、地址是否相同，都需要人工確認。
    backend_slot_groups = defaultdict(list)
    for order in backend_orders:
        backend_slot_groups[(order["region"], order["service_date"], order["service_time"])].append(order)
    for (slot_region, slot_date, slot_time), slot_orders in backend_slot_groups.items():
        if len(slot_orders) < 2:
            continue
        order_details = "；".join(
            f"{item['order_no']}（{item['name'] or '姓名不明'}／{item['address'] or '地址不明'}）"
            for item in slot_orders
        )
        result["backend_duplicate_slots"].append({
            "region": slot_region,
            "service_date": slot_date,
            "service_time": slot_time,
            "count": len(slot_orders),
            "order_nos": [item["order_no"] for item in slot_orders],
            "issue": (
                f"{slot_region}後台系統在 {slot_date} {slot_time} 有 {len(slot_orders)} 筆訂單："
                f"{order_details}。同一天同一時段系統有 2 筆以上，列為異常，請確認。"
            ),
        })

    # ---------- 方向一：後台有、日曆沒有 ----------
'''
s = replace_once(s, needle, replacement, "add duplicate slot result")

# 3) 精確配對必須同一人 + 同地址 + 同日期時段。
needle = '''            if _event_time_match(order, event) and (
                _event_phone_match(order["phone"], event)
                or _event_addr_core_match(order["address"], event)
            ):
'''
replacement = '''            if (
                _event_time_match(order, event)
                and _event_person_match(order, event)
                and _event_addr_core_match(order["address"], event)
            ):
'''
s = replace_once(s, needle, replacement, "tighten exact match")

# 4) 移除「只靠同日同時段」補配，避免不同地址／客人互相吃掉事件。
needle = '''        candidates = [
            e for e in calendar_events_by_region.get(order["region"], [])
            if e.get("id") not in matched_event_ids and _event_time_match(order, e)
        ]
        if candidates:
            matched_event_ids.add(candidates[0].get("id"))
            continue

'''
s = replace_once(s, needle, "", "remove time-only fallback")

# 5) 不再因「整段期間電話完全沒出現在日曆」而跳過；既然規則是同人/地址/日期/時段，就應列異常。
needle = '''        # v2026.08.14：後台有這筆訂單，但這支電話整段期間內完全沒出現在該區域
        # 日曆的任何一筆事件裡（不限時段、不限顏色）——代表這位客人根本不是走
        # 日曆管理流程（例如電話沒登記進日曆、或這類客人本來就不會排進這個
        # 日曆），不列入比對範圍，避免誤報成「日曆沒有」。
        if not _phone_in_any_event(order["phone"], all_events_by_region.get(order["region"], [])):
            continue

'''
s = replace_once(s, needle, "", "remove phone skip")

# 6) 異常文案不要再說「已被其他訂單配走」，直接說同時段事件不是同一人/地址。
s = replace_once(
    s,
    'parts.append(f"{len(yellow_events)} 筆是黃色，但同時段訂單數比黃色事件數多，已被其他訂單配走")',
    'parts.append(f"{len(yellow_events)} 筆是黃色，但沒有同時符合此訂單的客人與地址")',
    "replace misleading diagnostic",
)

# 7) 反向異常文案同步說明精確比對條件。
s = replace_once(
    s,
    'f"但後台這段期間的已付款訂單裡找不到服務日期／時段相符的訂單，"',
    'f"但後台這段期間的已付款訂單裡找不到同一人／地址／服務日期／時段皆相符的訂單，"',
    "reverse diagnostic",
)

# 8) 註解同步，避免未來又加回跨客戶補配。
s = s.replace(
    '''    # 第二輪：電話／地址都核對不上（或事件內容完全沒有可辨識資訊）的訂單，
    # 才用「同時段還沒被配走的事件」補配——此時才可能發生名不符實的配對，
    # 但至少不會搶走第一輪已經確認電話／地址相符的配對。
''',
    '''    # 第二輪：只處理沒有「同一人＋同地址＋同日期＋同時段」精確配對的訂單。
    # 不允許用同時段其他客人／其他地址的事件補配。
''',
    1,
)

orders_path.write_text(s, encoding="utf-8")

app_path = Path("ordersapp.py")
app = app_path.read_text(encoding="utf-8")
needle = '''        _backend_missing = cc_result.get("backend_missing_in_calendar") or []
        _calendar_missing = cc_result.get("calendar_missing_in_backend") or []
        if not _backend_missing and not _calendar_missing:
'''
replacement = '''        _backend_duplicates = cc_result.get("backend_duplicate_slots") or []
        _backend_missing = cc_result.get("backend_missing_in_calendar") or []
        _calendar_missing = cc_result.get("calendar_missing_in_backend") or []
        if not _backend_duplicates and not _backend_missing and not _calendar_missing:
'''
app = replace_once(app, needle, replacement, "app result vars")

needle = '''        else:
            if _backend_missing:
                st.error(f"⚠️ 後台有、日曆沒有：{len(_backend_missing)} 筆")
'''
replacement = '''        else:
            if _backend_duplicates:
                st.error(f"⚠️ 系統同日同時段多筆：{len(_backend_duplicates)} 組")
                for _p in _backend_duplicates:
                    st.warning(_p.get("issue", ""))
            if _backend_missing:
                st.error(f"⚠️ 後台有、日曆沒有：{len(_backend_missing)} 筆")
'''
app = replace_once(app, needle, replacement, "app duplicate display")
app_path.write_text(app, encoding="utf-8")

# 靜態驗證。
assert 'backend_duplicate_slots' in s
assert 'and _event_person_match(order, event)' in s
assert 'and _event_addr_core_match(order["address"], event)' in s
assert '已被其他訂單配走' not in s
assert '系統同日同時段多筆' in app
print("patch ok")
