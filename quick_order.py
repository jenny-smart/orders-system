# ============================================================
# 檔名：quick_order_7_7.py
# 版本：v7.7
# 模組：單筆服務訂單後端模組
# 建立日期：2026-06-22
# 最後更新：2026-06-24
#
# Change Log
# v7.7
# - 儲值金補價差拆成兩段：先建立儲值金折抵單，再建立客付補價差單。
# - 日期類型改由服務日期自動判斷：週一到週五為平日，週六日為週末。
# - 儲值金折抵單改用全額優惠券折抵，讓該單總金額為 0。
# - 保留 v7.6 對 stored_value_routine 回傳 count 的回查處理。
# v7.6
# - 修正 /booking/stored_value_routine 回傳 {"count":1} 時，改視為後台已受理並加強重試查詢新訂單編號。
# - 若後台已建立但訂單列表延遲更新，會依電話、日期、時段、地址、付款方式回查最可能的新訂單。
# - 程式檔頭版本與更新日期同步更新。
# v7.5
# - 補上儲值金補價差自動轉換主函式 stored_value_makeup_convert。
# - 儲值金折抵訂單金額：平日 600n、週末 700n，餘額 = 倍數金額 - 優惠券金額。
# - 支援建立客付訂單與發票欄位：二聯會員信箱/手機載具、三聯抬頭/統編。
# - 程式檔頭版本與更新日期同步更新。
# v7.4
# - 新增儲值金補價差自動轉換流程。
# v7.3
# - 新增 PERIOD_DISPLAY_INFO / _format_period_display()
# - build_line_message 服務時間加入時數/休息備註/人數
# - build_line_message_from_order_no 補 person / period_s
# - quick_create_order return dict 補 person / period_s
# v7.2
# - 新客資料拆解電話統一轉純數字
# - 新增發票抬頭與統編解析
# - 保留載具/統編資訊相容欄位
# ============================================================
# -*- coding: utf-8 -*-
__version__ = "7.7"

"""
單筆快速建單模組（信用卡 / ATM / 儲值金）

設計目的：
非定期、單次客人，不需要先把整列資料填進 Google Sheet。
電話查會員 → 帶出上次地址/服務內容/付款方式 → 算時數 → 直接建單 → 產生 LINE 文案。

直接 reuse orders.py 既有的後台互動 function，避免重複邏輯/重複維護。
"""
import time
import re
from datetime import date, datetime, timedelta

import requests

import orders  # 直接 import 模組本身，才能在執行前覆寫它的環境變數
from orders import (
    login,
    get_csrf_token,
    get_member,
    pick_best_address_info,
    geocode_address,
    check_contain,
    calculate_hour,
    extract_calc_fields,
    get_section_raw,
    slot_exists_in_section_response,
    extract_cleaners_from_section_response,
    format_staff_from_cleaners,
    fetch_order_meta_by_order_no,
    extract_order_cards_from_purchase_html,
    _extract_staff_line,
    send_confirmation_mail,
    normalize_phone,
    normalize_addr_for_match,
    display_period_text,
    first_nonzero,
    find_nested_value,
    get_region_by_address,
    HEADERS,
)
from accounts import ACCOUNTS
from env import BASE_URL_DEV, BASE_URL_PROD, ORDER_PREFIX_DEV, ORDER_PREFIX_PROD

# 後台 payway 欄位對照（沿用代客預訂單次表單的 select option value）
PAYWAY_MAP = {
    "信用卡": "1",
    "ATM": "2",
    "儲值金": "4",
}

# 不同付款方式對應的後台建單路徑
BOOKING_ENDPOINT_MAP = {
    "信用卡": "/booking/single",
    "ATM": "/booking/single",
    "儲值金": "/booking/stored_value_routine",
}

TAX_RATE = 1.05  # 服務費未稅 → 含稅

# 服務時段對應時數與中間休息備註
# has_break=True 的時段，LINE 訊息顯示「X小時,中間休息1小時」
PERIOD_DISPLAY_INFO = {
    "08:30-12:30": ("4小時", False),
    "09:00-11:00": ("2小時", False),
    "09:00-12:00": ("3小時", False),
    "14:00-16:00": ("2小時", False),
    "14:00-17:00": ("3小時", False),
    "14:00-18:00": ("4小時", False),
    "09:00-16:00": ("6小時", True),
    "09:00-18:00": ("8小時", True),
}


def _format_period_display(period_raw, person="", display_override=""):
    """
    格式化服務時段顯示。

    輸出格式：HH:MM-HH:MM（N人M小時）或 HH:MM-HH:MM（N人M小時，中間休息1小時）
    例：14:00-17:00 + 2人 → 14:00-17:00（2人3小時）
        09:00-18:00 + 3人 → 09:00-18:00（3人8小時，中間休息1小時）
        08:30-17:30（簡訊實際時間）+ 09:00-18:00（查對照表）→ 08:30-17:30（2人8小時，中間休息1小時）
    """
    compact = str(period_raw or "").replace(" ", "")
    display = str(display_override or "").replace(" ", "") or compact
    info = PERIOD_DISPLAY_INFO.get(compact)
    person_str = str(person or "").strip()
    if info:
        hour_str, has_break = info
        break_note = "，中間休息1小時" if has_break else ""
        if person_str and person_str != "0":
            inner = f"{person_str}人{hour_str}{break_note}"
        else:
            inner = f"{hour_str}{break_note}"
        return f"{display}（{inner}）"
    # 找不到對照表：只顯示時段與人數
    if person_str and person_str != "0":
        return f"{display}（{person_str}人）"
    return display


def _extract_actual_service_time(joined_text):
    """
    從訂單區塊文字抓「簡訊實際服務時間」。

    後台部分訂單會記錄實際到離場時間，格式為：
      簡訊實際服務時間\n08:30-17:30
    或
      簡訊實際服務時間 08:30 - 17:30

    找到時回傳正規化後的 "HH:MM - HH:MM" 字串（與 _parse_service_date_time_loose
    回傳格式一致）；找不到回傳空字串。
    """
    m = re.search(
        r"簡訊實際服務時間\s*[：:]?\s*(\d{1,2}:\d{2})\s*[-~～]\s*(\d{1,2}:\d{2})",
        joined_text,
    )

    if m:
        start, end = m.groups()
        return f"{start} - {end}"
    return ""


def _extract_phone_from_block_lines(lines):
    """
    從訂單區塊行列表抓電話號碼（台灣手機格式）。
    用於同日多筆訂單偵測時反查電話。
    """
    joined = "\n".join(lines)
    m = re.search(r"(?:\+?886[-\s]?)?0?9[\d\-\s]{8,10}", joined)
    if m:
        return normalize_phone(m.group(0))
    return ""


def _fetch_same_date_order_blocks(session, phone, target_date):
    """
    用電話搜尋「同一服務日期」的所有訂單區塊（不限付款狀態）。

    用途：輸入任一訂單號後，自動偵測同一客人同一天是否有其他訂單，
    若有則合併顯示服務時間，避免通知只寫其中一筆。

    注意：客人可能在不同日期都有待付款訂單，這個函式嚴格比對
    service_date == target_date，不會跨日合併。
    """
    blocks = _fetch_purchase_blocks_for_phone(session, phone)
    same_date = []
    for block in blocks:
        lines = block.get("lines", [])
        joined = "\n".join(lines)
        service_date, _ = _parse_service_date_time_loose(joined)
        if service_date == target_date:
            same_date.append(block)
    return same_date


def _build_combined_period_display(orders_data):
    """
    將同日多筆訂單的服務時段合併成一段描述字串。

    orders_data: list of dicts，每筆包含：
        period_s      原始預約時段（查 PERIOD_DISPLAY_INFO）
        actual_period 簡訊實際服務時間（有值時覆蓋顯示）
        person        人數

    回傳格式範例：
        14:00-17:00（2人3小時）＋08:30-12:30（2人4小時），共14人時
    """
    parts = []
    total_ph = 0
    for o in sorted(orders_data, key=lambda x: str(x.get("period_s") or "").replace(" ", "")):
        period_raw = str(o.get("period_s") or "").replace(" ", "")
        actual = str(o.get("actual_period") or "").replace(" ", "")
        person_str = str(o.get("person") or "").strip()
        p_str = _format_period_display(period_raw, person_str, display_override=actual)
        parts.append(p_str)
        info = PERIOD_DISPLAY_INFO.get(period_raw)
        if info:
            try:
                h = int(float(info[0].replace("小時", "")))
                p = int(person_str) if person_str else 0
                total_ph += h * p
            except Exception:
                pass

    combined = "＋".join(parts)
    if total_ph:
        combined += f"，共{total_ph}人時"
    return combined


def _configure_environment(env_name):
    """
    重要修正：
    orders.py 裡 login()/get_member()/check_contain() 等 function，
    內部用的是 orders.py 模組層級的 LOGIN_URL / GET_MEMBER_URL ... 全域變數，
    只有 run_process_web() 會重新賦值。

    單筆建單流程完全沒有呼叫 run_process_web()，
    所以一直打的是「import 當下 env.py 設定的環境」，跟畫面選的 dev/prod 無關。

    這裡直接覆寫 orders 模組的全域變數，確保 dev/prod 真的會切換。
    """
    base_url = BASE_URL_DEV if env_name == "dev" else BASE_URL_PROD
    order_prefix = ORDER_PREFIX_DEV if env_name == "dev" else ORDER_PREFIX_PROD

    orders.BASE_URL = base_url
    orders.ORDER_PREFIX = order_prefix
    orders.LOGIN_URL = f"{base_url}/login"
    orders.BOOKING_URL = f"{base_url}/booking/stored_value_routine"
    orders.PURCHASE_URL = f"{base_url}/purchase"
    orders.GET_MEMBER_URL = f"{base_url}/ajax/get_member"
    orders.CHECK_CONTAIN_URL = f"{base_url}/ajax/check_contain"
    orders.CALCULATE_HOUR_URL = f"{base_url}/ajax/calculate_hour"
    orders.GET_SECTION_URL = f"{base_url}/ajax/get_section"
    orders.MAIL_SUCCESS_URL = f"{base_url}/purchase/mail_success/{{order_no}}"

    return base_url


def _booking_url_for_payway(base_url, payway):
    return f"{base_url}{BOOKING_ENDPOINT_MAP.get(payway, '/booking/single')}"


def _get_booking_token_for_payway(session, base_url, payway):
    orders.BOOKING_URL = _booking_url_for_payway(base_url, payway)
    return get_csrf_token(session)


def _build_booking_submit_data(base_data, token, payway, slot):
    data = {**base_data, "_token": token}
    if payway in ("信用卡", "ATM"):
        data["date_s"] = ""
        data["datePeriod"] = slot
    else:
        data["date_list[]"] = [slot]
    return data


def quick_lookup_member(env_name, backend_email, backend_password, phone, clean_type_id="1"):
    """
    電話查會員。回傳 session/token 給後續單筆建單共用，
    避免每個步驟都重新登入。

    member_payload 為 None 代表查無此會員（新客人），
    呼叫端應該改走「新客人資訊收集」流程，不能呼叫 quick_create_order。
    """
    base_url = _configure_environment(env_name)

    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    token = get_csrf_token(session)

    phone = normalize_phone(phone)
    member_payload = get_member(session, phone, token, clean_type_id)

    return {
        "session": session,
        "token": token,
        "phone": phone,
        "member_payload": member_payload,
        "base_url": base_url,
        "env_name": env_name,
    }


PURCHASE_FILTER_PARAMS_TEMPLATE = {
    "keyword": "",
    "name": "",
    "phone": "",
    "orderNo": "",
    "date_s": "",
    "date_e": "",
    "clean_date_s": "",
    "clean_date_e": "",
    "paid_at_s": "",
    "paid_at_e": "",
    "refundDateS": "",
    "refundDateE": "",
    "buy": "",
    "area_id": "",
    "isCharge": "",
    "isRefund": "",
    "payway": "",
    "purchase_status": "",
    "progress_status": "",
    "invoiceStatus": "",
    "otherFee": "",
    "orderBy": "",
}


_LAST_PURCHASE_FETCH_DEBUG = {}
PURCHASE_STATUS_PAID = "1"


def get_last_purchase_fetch_debug():
    """取得最近一次 _fetch_purchase_blocks_for_phone 的診斷資訊，供畫面顯示除錯用。"""
    return dict(_LAST_PURCHASE_FETCH_DEBUG)


def _block_matches_phone_filter(block, phone_norm):
    """
    後台已用 phone= 篩選時，列表文字不一定會完整顯示電話。
    若卡片內有電話文字就二次確認；若沒有電話文字，信任後台篩選結果。
    """
    if not phone_norm:
        return True

    joined = "\n".join(block.get("lines", []))
    compact = joined.replace("-", "").replace(" ", "")
    if phone_norm in compact:
        return True

    visible_phones = {
        normalize_phone(m.group(0))
        for m in re.finditer(r"(?:\+?886[-\s]?)?0?9[\d\-\s]{8,12}", joined)
    }
    visible_phones.discard("")
    if visible_phones:
        return phone_norm in visible_phones

    return True


def _fetch_purchase_blocks_for_phone(session, phone, name="", purchase_status=""):
    """
    比照後台「訂單管理」篩選列搜尋的實際請求格式。
    """
    global _LAST_PURCHASE_FETCH_DEBUG

    params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
    params["phone"] = normalize_phone(phone)
    if purchase_status:
        params["purchase_status"] = purchase_status
    if name and not params["phone"]:
        params["name"] = name

    resp = session.get(orders.PURCHASE_URL, params=params, headers=HEADERS, allow_redirects=True)

    raw_blocks = []
    if resp.status_code == 200:
        raw_blocks = extract_order_cards_from_purchase_html(resp.text)

    looks_like_login_page = "login" in resp.url.lower() or (
        len(raw_blocks) == 0 and "password" in resp.text.lower()
    )
    effective_purchase_status = purchase_status
    fallback_info = {}

    if purchase_status and resp.status_code == 200 and not raw_blocks and not looks_like_login_page:
        fallback_params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
        fallback_params["phone"] = normalize_phone(phone)
        if name and not fallback_params["phone"]:
            fallback_params["name"] = name

        fallback_resp = session.get(
            orders.PURCHASE_URL,
            params=fallback_params,
            headers=HEADERS,
            allow_redirects=True,
        )
        fallback_blocks = []
        if fallback_resp.status_code == 200:
            fallback_blocks = extract_order_cards_from_purchase_html(fallback_resp.text)

        fallback_info = {
            "fallback_request_url": getattr(fallback_resp.request, "url", ""),
            "fallback_status_code": fallback_resp.status_code,
            "fallback_raw_block_count": len(fallback_blocks),
        }

        if fallback_blocks:
            resp = fallback_resp
            raw_blocks = fallback_blocks
            effective_purchase_status = ""
            looks_like_login_page = "login" in resp.url.lower()

    _LAST_PURCHASE_FETCH_DEBUG = {
        "request_url": getattr(resp.request, "url", ""),
        "final_url": resp.url,
        "status_code": resp.status_code,
        "purchase_status_filter": purchase_status,
        "effective_purchase_status_filter": effective_purchase_status,
        "raw_block_count": len(raw_blocks),
        "looks_like_login_page": looks_like_login_page,
        "snippet": resp.text[:300].replace("\n", " ").strip() if resp.status_code == 200 else "",
        **fallback_info,
    }

    if resp.status_code != 200:
        return []

    phone_norm = normalize_phone(phone)
    if not phone_norm:
        _LAST_PURCHASE_FETCH_DEBUG["filtered_block_count"] = len(raw_blocks)
        return raw_blocks

    filtered = []
    for block in raw_blocks:
        if _block_matches_phone_filter(block, phone_norm):
            filtered.append(block)

    _LAST_PURCHASE_FETCH_DEBUG["filtered_block_count"] = len(filtered)
    return filtered


def list_order_numbers_for_phone(session, phone, name=""):
    """
    取得「這支電話」目前所有訂單編號集合。
    用於送出建單前後比對，確認真的有新訂單產生。
    """
    blocks = _fetch_purchase_blocks_for_phone(session, phone, name=name)
    return {block["order_no"] for block in blocks if block.get("order_no")}


def _fetch_purchase_block_for_order_no(session, order_no):
    params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
    params["orderNo"] = str(order_no or "").strip()
    resp = session.get(orders.PURCHASE_URL, params=params, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        raise Exception(f"查詢訂單失敗：HTTP {resp.status_code}")

    target = str(order_no or "").strip()
    for block in extract_order_cards_from_purchase_html(resp.text):
        if block.get("order_no") == target:
            return block

    raise Exception(f"查無訂單：{target}")


def _parse_service_date_time_loose(joined_text):
    """
    從訂單區塊文字解析「服務日期」與「服務時段」。
    回傳 (日期, "起 - 迄" 時段字串)，抓不到回傳 ("", "")。
    """
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*[（(][一二三四五六日][）)]", joined_text)

    if not date_match:
        for m in re.finditer(r"(\d{4}-\d{2}-\d{2})", joined_text):
            tail = joined_text[m.end():m.end() + 12]
            if not re.match(r"\s*\d{1,2}:\d{2}:\d{2}", tail):
                date_match = m
                break

    if not date_match:
        return "", ""

    service_date = date_match.group(1)

    tail = joined_text[date_match.end():date_match.end() + 600]
    time_match = re.search(r"(\d{1,2}:\d{2})\s*[-~～]\s*(\d{1,2}:\d{2})(?!:\d)", tail)
    if not time_match:
        time_match = re.search(r"(\d{1,2}:\d{2})\s*[-~～]\s*(\d{1,2}:\d{2})(?!:\d)", joined_text)
    if not time_match:
        return service_date, ""

    start, end = time_match.groups()
    return service_date, f"{start} - {end}"


def _extract_money_line(joined_text, labels):
    text = str(joined_text or "").replace(",", "")
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*[：:]?\s*\$?\s*(-?\d+(?:\.\d+)?)", text)
        if m:
            value = m.group(1)
            try:
                number = float(value)
                return str(int(number)) if number.is_integer() else str(number)
            except Exception:
                return value
    return ""


def _extract_total_amount_line(joined_text):
    return _extract_money_line(joined_text, ["訂單總金額", "總金額", "合計", "總計"])


def _extract_fare_line(joined_text):
    return _extract_money_line(joined_text, ["車馬費"])


def _extract_person_hour_line(joined_text):
    text = str(joined_text or "")

    compact_match = re.search(r"(\d+)\s*人\s*(\d+(?:\.\d+)?)\s*(?:小時|時)", text)
    if compact_match:
        return compact_match.group(1), compact_match.group(2)

    person = ""
    hour = ""
    person_match = re.search(r"(?:服務人數|人數|專員人數)\s*[：:]?\s*(\d+)", text)
    hour_match = re.search(r"(?:服務時數|時數)\s*[：:]?\s*(\d+(?:\.\d+)?)", text)
    if person_match:
        person = person_match.group(1)
    if hour_match:
        hour = hour_match.group(1)
    return person, hour


def _count_staff_from_lines(lines):
    """
    從訂單區塊數服務人員人數，作為人數欄位的備用來源。

    使用 _extract_staff_line() 取得人員字串（格式：嚴慶隆 X 林岱羽 X 塗敏捷），
    以空白+X+空白分隔，計算非「檸檬人」的人數。
    """
    staff_str = _extract_staff_line(lines)
    if not staff_str:
        return ""
    # 以 X（前後可能有空白）分隔
    parts = [p.strip() for p in re.split(r"\s*X\s*", staff_str) if p.strip()]
    # 排除檸檬人（學員）
    count = sum(1 for p in parts if "檸檬人" not in p)
    return str(count) if count > 0 else ""


def _extract_address_line(lines):
    for line in lines:
        text = str(line or "").strip()
        if not text or "@" in text or text.upper() == "LINE":
            continue
        if re.search(r"(台|臺|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|嘉義|苗栗|彰化|南投|雲林|屏東|宜蘭|花蓮|台東|臺東|澎湖|金門|連江).*(市|縣).*(區|鄉|鎮|市)", text):
            return text
    return ""


def _date_not_after_today(date_text):
    try:
        return datetime.strptime(str(date_text), "%Y-%m-%d").date() <= date.today()
    except Exception:
        return False


def _extract_payway_line(joined_text):
    """
    從訂單區塊文字解析「付款方式」。
    """
    m = re.search(r"付款方式[：:]\s*([^\s\n]+)", joined_text)
    if m:
        value = m.group(1).strip()
        if value in ("信用卡", "ATM"):
            return value
    if "儲值金" in joined_text:
        return "儲值金"
    if _extract_total_amount_line(joined_text) == "0" and not _extract_invoice_line(joined_text):
        return "儲值金"
    return ""


def _service_amount_from_block(joined_text, fare):
    total = _extract_total_amount_line(joined_text)
    if not total:
        return ""
    try:
        total_num = int(round(float(str(total).replace(",", ""))))
        fare_num = int(round(float(str(fare or "0").replace(",", ""))))
        amount = total_num - fare_num if fare_num and total_num > fare_num else total_num
        return str(amount)
    except Exception:
        return total


def build_combined_line_message_from_order_nos(
    env_name,
    backend_email,
    backend_password,
    order_nos,
    fallback_region="台北",
):
    """
    多筆訂單合併成一則 LINE 通知。

    用於客服在輸入框以「+」分隔指定同日合併單的情況，
    例如：LC002115751+LC002115741 → 合併成一則含累計金額與合併時段的訊息。

    驗證：所有訂單需同一服務日期、同一付款方式，否則拋出說明例外。
    """
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    orders_info = []
    for ono in order_nos:
        block = _fetch_purchase_block_for_order_no(session, ono)
        lines = block.get("lines", [])
        joined = "\n".join(lines)

        service_date, service_time = _parse_service_date_time_loose(joined)
        actual_time = _extract_actual_service_time(joined)
        person_extracted, _ = _extract_person_hour_line(joined)
        if not person_extracted:
            person_extracted = _count_staff_from_lines(lines)
        address = _extract_address_line(lines)
        fare = _extract_fare_line(joined) or "0"
        payway = _extract_payway_line(joined)
        service_amount = _service_amount_from_block(joined, fare)
        region = get_region_by_address(address, ACCOUNTS) or fallback_region

        if not service_date or not service_time:
            raise Exception(f"訂單 {ono} 缺少服務日期或時段，無法合併")
        if not address:
            raise Exception(f"訂單 {ono} 缺少服務地址，無法合併")
        if not payway:
            raise Exception(f"訂單 {ono} 無法判斷付款方式，請至後台確認")

        orders_info.append({
            "order_no": ono,
            "service_date": service_date,
            "period_s": service_time,
            "actual_period": actual_time,
            "person": person_extracted,
            "address": address,
            "fare": fare,
            "payway": payway,
            "service_amount": service_amount,
            "region": region,
        })

    # 付款方式驗證（不同付款方式不允許合併）
    payways = {o["payway"] for o in orders_info if o["payway"]}
    if len(payways) > 1:
        raise Exception(
            f"合併的訂單付款方式不同（{', '.join(payways)}），請分開輸入分別產生通知。"
        )

    # ── 服務時間顯示：三種組合 ─────────────────────────────────────────
    unique_dates = sorted({o["service_date"] for o in orders_info})
    all_same_date = len(unique_dates) == 1

    if all_same_date:
        # 同日不同時段 → 日期由模板帶，時段合併一行
        period_data = [
            {"period_s": o["period_s"], "actual_period": o["actual_period"], "person": o["person"]}
            for o in orders_info
        ]
        combined_period = _build_combined_period_display(period_data)
        multi_date = False

    else:
        # 不同日期、不同時段 → 每行各自列日期＋時段
        period_lines = []
        for o in orders_info:
            d = o["service_date"].replace("-", "/")
            p_raw = str(o["period_s"] or "").replace(" ", "")
            p_actual = str(o["actual_period"] or "").replace(" ", "")
            p_person = str(o["person"] or "")
            p_str = _format_period_display(p_raw, p_person, display_override=p_actual)
            period_lines.append(f"{d} {p_str}")
        combined_period = "\n".join(period_lines)
        multi_date = True

    # ── 金額：A＋B＝總計 ──────────────────────────────────────────────
    amount_parts = []
    total_amount = 0
    for o in orders_info:
        try:
            v = int(str(o["service_amount"] or "0").replace(",", ""))
            amount_parts.append(str(v))
            total_amount += v
        except Exception:
            pass
    if len(amount_parts) > 1:
        amount_display = "＋".join(amount_parts) + "＝" + str(total_amount)
    else:
        amount_display = str(total_amount) if total_amount else ""

    # ── 車馬費加總 ────────────────────────────────────────────────────
    total_fare = 0
    for o in orders_info:
        try:
            total_fare += int(str(o["fare"] or "0").replace(",", ""))
        except Exception:
            pass

    first = orders_info[0]
    result = {
        "order_no": first["order_no"],
        "all_order_nos": order_nos,
        "address": first["address"],
        "date": first["service_date"],
        "period": first["period_s"],
        "period_s": first["period_s"],
        "actual_period": first["actual_period"],
        "combined_period": combined_period,
        "multi_date": multi_date,
        "person": first["person"],
        "service_amount": amount_display,
        "price_with_tax": str(total_amount),
        "fare": str(total_fare) if total_fare else "0",
        "payway": first["payway"],
        "region": first["region"],
        "env_name": env_name,
        "session": session,
        "source_url": f"{base_url}/purchase?orderNo={first['order_no']}",
    }
    return result, build_line_message(result)


def build_line_message_from_order_no(
    env_name,
    backend_email,
    backend_password,
    order_no,
    fallback_region="台北",
):
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    block = _fetch_purchase_block_for_order_no(session, order_no)
    lines = block.get("lines", [])
    joined = "\n".join(lines)

    service_date, service_time = _parse_service_date_time_loose(joined)
    # v7.4: 若有「簡訊實際服務時間」，優先用於 LINE 訊息顯示
    actual_time = _extract_actual_service_time(joined)
    # v7.3: 從訂單區塊抓人數；若文字中無「X人Y小時」，改從服務人員名單數人頭
    person_extracted, _ = _extract_person_hour_line(joined)
    if not person_extracted:
        person_extracted = _count_staff_from_lines(lines)
    address = _extract_address_line(lines)
    fare = _extract_fare_line(joined) or "0"
    payway = _extract_payway_line(joined)
    region = get_region_by_address(address, ACCOUNTS) or fallback_region
    service_amount = _service_amount_from_block(joined, fare)

    if not service_date or not service_time:
        raise Exception(f"訂單 {order_no} 缺少服務日期或時段，無法產生通知")
    if not address:
        raise Exception(f"訂單 {order_no} 缺少服務地址，無法產生通知")
    if not payway:
        raise Exception(f"訂單 {order_no} 無法判斷付款方式（信用卡/ATM/儲值金），請至後台『訂單管理』確認後手動處理，不要使用本功能猜測。")
    if payway != "儲值金" and not service_amount:
        raise Exception(f"訂單 {order_no} 缺少服務金額，無法產生通知")

    result = {
        "order_no": block["order_no"],
        "all_order_nos": [block["order_no"]],
        "address": address,
        "date": service_date,
        "period": service_time,
        "period_s": service_time,
        "actual_period": actual_time,
        "combined_period": "",
        "person": person_extracted,
        "service_amount": service_amount,
        "price_with_tax": service_amount,
        "fare": fare,
        "payway": payway,
        "region": region,
        "env_name": env_name,
        "session": session,
        "source_url": f"{base_url}/purchase?orderNo={block['order_no']}",
    }
    return result, build_line_message(result)


def quick_check_available_slots(
    env_name,
    payway,
    lookup_result,
    address,
    clean_type_id,
    date_s,
    hour,
    person="2",
    periods=None,
    period_hours=None,
):
    """
    用目前單筆建單表單資料向後台查班表，但不建立訂單。
    回傳每個時段是否有班表與可解析到的服務人員。
    """
    base_url = _configure_environment(env_name)
    session = lookup_result["session"]
    token = _get_booking_token_for_payway(session, base_url, payway)
    member_payload = lookup_result["member_payload"]

    if not member_payload:
        raise Exception("此電話查無會員資料，請先查詢會員")

    member = member_payload.get("member", {})
    best_addr = pick_best_address_info(member_payload, address)
    if not best_addr:
        raise Exception(f"找不到對應地址資料：{address}")

    selected_address = str(best_addr.get("address") or address).strip()
    geo_lat, geo_lng = geocode_address(selected_address)
    if geo_lat and geo_lng:
        best_addr["lat"] = geo_lat
        best_addr["lng"] = geo_lng

    addr_check = check_contain(
        session,
        member.get("member_id", ""),
        selected_address,
        best_addr.get("lat", ""),
        best_addr.get("lng", ""),
        token,
        clean_type_id,
    )
    if not addr_check and lookup_result.get("token") and lookup_result.get("token") != token:
        addr_check = check_contain(
            session,
            member.get("member_id", ""),
            selected_address,
            best_addr.get("lat", ""),
            best_addr.get("lng", ""),
            lookup_result["token"],
            clean_type_id,
        )
    if not addr_check:
        raise Exception(f"查詢地址/地區失敗：{selected_address}")

    area_info = addr_check.get("area") if isinstance(addr_check.get("area"), dict) else {}
    if area_info:
        best_addr["area_id"] = area_info.get("area_id", best_addr.get("area_id"))
        best_addr["company_id"] = area_info.get("company_id", best_addr.get("company_id"))
        best_addr["country_id"] = area_info.get("country_id", best_addr.get("country_id"))

    old_purchase = best_addr.get("purchase", {}) if isinstance(best_addr.get("purchase"), dict) else {}

    def pick(key, default=""):
        value = old_purchase.get(key)
        return value if value not in (None, "") else default

    base_data = {
        "clean_type_id": clean_type_id,
        "phone": lookup_result.get("phone", ""),
        "name": str(member.get("name") or "").strip(),
        "email": str(member.get("email") or "").strip(),
        "tel": str(member.get("tel") or lookup_result.get("phone", "")),
        "line": str(member.get("line") or ""),
        "fbName": str(member.get("fb_name") or ""),
        "fb": str(member.get("fb") or ""),
        "memoProcess": str(member.get("memo_process") or ""),
        "memoFinance": str(member.get("memo_finance") or ""),
        "addressId": str(best_addr.get("addressId") or ""),
        "country_id": str(best_addr.get("country_id") or pick("country_id", "12")),
        "address": selected_address,
        "ping": str(pick("ping", "4")),
        "room": str(pick("room", "0")),
        "bathroom": str(pick("bathroom", "0")),
        "balcony": str(pick("balcony", "0")),
        "livingroom": str(pick("livingroom", "0")),
        "kitchen": str(pick("kitchen", "0")),
        "window": str(pick("window", "")),
        "shutter": str(pick("shutter", "")),
        "clothes": str(pick("clothes", "0")),
        "dyson": str(pick("dyson", "0")),
        "refrigerator": str(pick("refrigerator", "0")),
        "disinfection": str(pick("disinfection", "0")),
        "go_abord": str(pick("go_abord", "0")),
        "home_move": str(pick("home_move", "0")),
        "storage": str(pick("storage", "0")),
        "cabinet": str(pick("cabinet", "0")),
        "quintuple": str(pick("quintuple", "0")),
        "hour": str(int(float(hour))),
        "price": "",
        "price_vvip": "",
        "person": str(person),
        "date_s": date_s,
        "period_s": "",
        "period": "",
        "cycle": "1",
        "fare": "",
        "memo": "",
        "notice": str(best_addr.get("notice") or old_purchase.get("notice") or ""),
        "discount_code": "",
        "payway": PAYWAY_MAP.get(payway, "2"),
        "invoice_type": "2",
        "carrier_type_id": "1",
        "carrier_info": str(member.get("email") or ""),
        "company_title": "",
        "company_no": "",
        "donate_code": "8585",
        "is_backend": "477",
        "member_id": str(member.get("member_id") or ""),
        "company_id": str(best_addr.get("company_id") or pick("company_id", "1")),
        "area_id": str(best_addr.get("area_id") or pick("area_id", "25")),
        "lat": str(best_addr.get("lat") or pick("lat", "")),
        "lng": str(best_addr.get("lng") or pick("lng", "")),
    }

    rows = []
    for period in periods or []:
        slot = f"{date_s}_{period}"
        data = base_data.copy()
        data["period_s"] = period
        data["hour"] = str(int(float((period_hours or {}).get(period, hour))))
        calc_result = calculate_hour(session, data, token)
        if not calc_result:
            rows.append({
                "date": date_s,
                "period": period,
                "available": False,
                "staff": "",
                "error": "計算時數失敗",
            })
            continue
        calc_fields = extract_calc_fields(calc_result, fallback_hours=data["hour"], fallback_fare="0")
        data["price"] = str(calc_fields.get("price") or "0")
        data["price_vvip"] = str(calc_fields.get("price_vvip") or "0")
        data["fare"] = str(calc_fields.get("fare") or "0")
        raw_section = get_section_raw(session, data, token, slot)
        available = slot_exists_in_section_response(raw_section, slot)
        cleaners = extract_cleaners_from_section_response(raw_section, slot) if available else []
        rows.append({
            "date": date_s,
            "period": period,
            "available": available,
            "staff": format_staff_from_cleaners(cleaners, people=person) if available else "",
        })
    return rows


def _is_paid_order_text(joined_text, trusted_paid_filter=False):
    if "已取消" in joined_text or "已退款" in joined_text:
        return False
    if trusted_paid_filter:
        return True
    compact = re.sub(r"\s+", "", str(joined_text or ""))
    if "待付款" in compact or "未付款" in compact:
        return False
    if "已付款" in compact:
        return True
    if "儲值金" in compact and (
        _extract_total_amount_line(joined_text) == "0"
        or "扣儲值金" in compact
        or "儲值金扣款" in compact
    ):
        return True
    if re.search(r"付款.{0,12}(完成|成功)", compact):
        return True
    return False


def _extract_invoice_line(joined_text):
    """從訂單區塊文字解析發票顯示文字（二聯式/三聯式/捐贈發票那一行），純文字顯示用。"""
    m = re.search(r"((?:二聯式|三聯式|捐贈發票)[：:][^\n]*)", joined_text)
    return m.group(1).strip() if m else ""


CLEAN_TYPE_LABELS = ["居家清潔", "辦公室清潔", "裝修細清", "大掃除"]


def _extract_clean_type_line(joined_text):
    """從訂單區塊文字解析服務類別（居家清潔/辦公室清潔/裝修細清/大掃除）。"""
    for label in CLEAN_TYPE_LABELS:
        if label in joined_text:
            return label
    return ""


def _extract_label_value(lines, label, stop_labels):
    """
    抓「標籤」獨立一行、值接在後面（可能空白）的欄位，例如客服備註/財務備註。
    抓不到或值為空白都回傳空字串。
    """
    try:
        idx = lines.index(label)
    except ValueError:
        return ""
    value_lines = []
    for line in lines[idx + 1:]:
        if line in stop_labels or line in CLEAN_TYPE_LABELS:
            break
        value_lines.append(line)
    return " ".join(value_lines).strip()


def get_customer_paid_orders(session, phone, known_addresses=None, name=""):
    """
    抓這支電話「所有已付款」訂單，不限地址、不限服務類別、不限付款方式。
    由新到舊排序。
    """
    known_addresses = known_addresses or []
    blocks = _fetch_purchase_blocks_for_phone(
        session,
        phone,
        name=name,
        purchase_status=PURCHASE_STATUS_PAID,
    )
    trusted_paid_filter = (
        get_last_purchase_fetch_debug().get("effective_purchase_status_filter") == PURCHASE_STATUS_PAID
    )

    results = []
    for block in blocks:
        lines = block.get("lines", [])
        joined = "\n".join(lines)

        if not _is_paid_order_text(joined, trusted_paid_filter=trusted_paid_filter):
            continue

        service_date, service_time = _parse_service_date_time_loose(joined)
        if not service_date:
            continue

        joined_norm = normalize_addr_for_match(joined)
        matched_addr = ""
        for addr in known_addresses:
            if addr and normalize_addr_for_match(addr) in joined_norm:
                matched_addr = addr
                break
        person, hour = _extract_person_hour_line(joined)
        payway = _extract_payway_line(joined)
        invoice_text = "" if payway == "儲值金" else _extract_invoice_line(joined)

        results.append({
            "order_no": block["order_no"],
            "date": service_date,
            "time": service_time,
            "address": matched_addr,
            "clean_type": _extract_clean_type_line(joined),
            "staff": _extract_staff_line(lines),
            "payway": payway,
            "invoice_text": invoice_text,
            "service_notice": _extract_label_value(lines, "客服備註", ["財務備註", "客人備註"]),
            "person": person,
            "hour": hour,
            "total_amount": _extract_total_amount_line(joined),
            "fare": _extract_fare_line(joined),
        })

    results.sort(key=lambda x: (x["date"], x.get("time", "")), reverse=True)
    return results


def _order_person_hour(member_payload, order):
    person, hour = _match_person_hour(member_payload, order.get("order_no", ""), order.get("address", ""))
    return person or order.get("person", ""), hour or order.get("hour", "")


def _match_person_hour(member_payload, order_no, address):
    """
    人數/時數訂單列表頁面本身不會顯示，要去會員 JSON
    （lastPurchase / 該地址的 purchase 物件）配對 order_no 才抓得到。
    """
    if not isinstance(member_payload, dict):
        return "", ""

    last_purchase = member_payload.get("lastPurchase", {}) or {}
    member = member_payload.get("member", {}) or {}
    addr_list = member.get("memberAddressList", []) or []

    candidates = []
    if last_purchase:
        candidates.append(last_purchase)

    target_norm = normalize_addr_for_match(address) if address else ""
    for item in addr_list:
        if target_norm and normalize_addr_for_match(item.get("address", "")) == target_norm:
            item_purchase = item.get("purchase", {})
            if isinstance(item_purchase, dict) and item_purchase:
                candidates.append(item_purchase)

    for c in candidates:
        if str(c.get("order_no", "")).strip() == str(order_no).strip():
            return c.get("person", ""), c.get("hour", "")

    return "", ""


def get_last_paid_per_address(session, phone, member_payload, known_addresses, within_days=365):
    """
    客人有多個地址時，每個地址各自找「近一年內」最近一次已付款服務。
    """
    cutoff = date.today() - timedelta(days=within_days)
    name = (member_payload.get("member", {}) or {}).get("name", "") if isinstance(member_payload, dict) else ""
    paid_orders = get_customer_paid_orders(session, phone, known_addresses, name=name)

    by_address = {}
    for o in paid_orders:
        addr = o.get("address", "")
        if not addr:
            continue
        if addr in by_address:
            continue
        try:
            d = datetime.strptime(o["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if d > date.today():
            continue
        if d < cutoff:
            continue
        by_address[addr] = o

    result = {}
    for addr in known_addresses:
        order = by_address.get(addr)
        if not order:
            result[addr] = None
            continue
        person, hour = _order_person_hour(member_payload, order)
        result[addr] = {
            "order_no": order["order_no"],
            "date": order["date"],
            "time": order["time"],
            "clean_type": order["clean_type"],
            "staff": order["staff"],
            "payway": order["payway"],
            "invoice_text": order["invoice_text"],
            "service_notice": order["service_notice"],
            "person": person,
            "hour": hour,
            "fare": order.get("fare", ""),
        }
    return result


def get_last_paid_summary(session, phone, member_payload, known_addresses):
    """
    取得「這支電話」全部地址、全部服務類別中，最近一次已付款服務的完整摘要。
    """
    name = (member_payload.get("member", {}) or {}).get("name", "") if isinstance(member_payload, dict) else ""
    paid_orders = get_customer_paid_orders(session, phone, known_addresses, name=name)
    paid_orders = [
        o for o in paid_orders
        if _date_not_after_today(o.get("date", ""))
    ]
    if not paid_orders:
        return None

    latest = paid_orders[0]
    same_date_orders = []
    for order in paid_orders:
        if order["date"] != latest["date"]:
            continue
        person, hour = _order_person_hour(member_payload, order)
        enriched = dict(order)
        enriched["person"] = person
        enriched["hour"] = hour
        same_date_orders.append(enriched)

    person, hour = _order_person_hour(member_payload, latest)

    return {
        "order_no": latest["order_no"],
        "date": latest["date"],
        "time": latest["time"],
        "address": latest["address"],
        "clean_type": latest["clean_type"],
        "staff": latest["staff"],
        "payway": latest["payway"],
        "invoice_text": latest["invoice_text"],
        "service_notice": latest["service_notice"],
        "person": person,
        "hour": hour,
        "fare": latest.get("fare", ""),
        "same_date_orders": same_date_orders,
    }


def get_unserved_paid_orders(session, phone, member_payload, known_addresses, today_value=None):
    """
    「已付款但服務日期還沒到」的訂單清單（排除已取消）。
    """
    today_value = today_value or date.today()
    name = (member_payload.get("member", {}) or {}).get("name", "") if isinstance(member_payload, dict) else ""
    blocks = _fetch_purchase_blocks_for_phone(
        session,
        phone,
        name=name,
        purchase_status=PURCHASE_STATUS_PAID,
    )
    trusted_paid_filter = (
        get_last_purchase_fetch_debug().get("effective_purchase_status_filter") == PURCHASE_STATUS_PAID
    )

    upcoming = []
    for block in blocks:
        lines = block.get("lines", [])
        joined = "\n".join(lines)

        if not _is_paid_order_text(joined, trusted_paid_filter=trusted_paid_filter):
            continue

        service_date, service_time = _parse_service_date_time_loose(joined)
        if not service_date:
            continue

        try:
            d = datetime.strptime(service_date, "%Y-%m-%d").date()
        except Exception:
            continue

        if d < today_value:
            continue

        joined_norm = normalize_addr_for_match(joined)
        matched_addr = ""
        for addr in known_addresses or []:
            if addr and normalize_addr_for_match(addr) in joined_norm:
                matched_addr = addr
                break

        payway = _extract_payway_line(joined)
        parsed_person, parsed_hour = _extract_person_hour_line(joined)
        person, hour = _match_person_hour(member_payload, block["order_no"], matched_addr)
        person = person or parsed_person
        hour = hour or parsed_hour

        upcoming.append({
            "order_no": block["order_no"],
            "date": service_date,
            "time": service_time,
            "address": matched_addr,
            "clean_type": _extract_clean_type_line(joined),
            "staff": _extract_staff_line(lines),
            "payway": payway,
            "invoice_text": "" if payway == "儲值金" else _extract_invoice_line(joined),
            "person": person,
            "hour": hour,
            "fare": _extract_fare_line(joined),
        })

    upcoming.sort(key=lambda x: x["date"])
    return upcoming


def quick_create_order(
    env_name,
    payway,
    region,
    lookup_result,
    address,
    clean_type_id,
    date_s,
    period_s,
    hour,
    person="2",
    fallback_fare="0",
    discount_code="",
    payment_type="",
    carrier_info="",
    company_no="",
    company_title="",
    invoice_type_override="",
    carrier_type_id_override="",
):
    """
    建立單筆訂單。流程完全比照人工在後台操作：
    查地址 → 算時數 → 查班表 → 送出 → 抓訂單編號 → 抓服務人員/狀態。
    """
    base_url = _configure_environment(env_name)

    session = lookup_result["session"]
    token = _get_booking_token_for_payway(session, base_url, payway)
    member_payload = lookup_result["member_payload"]
    phone = lookup_result["phone"]
    member_name = (member_payload.get("member", {}) or {}).get("name", "") if member_payload else ""

    if not member_payload:
        raise Exception("此電話查無會員資料，請先走新客人資訊收集流程建立會員後再建單")

    member = member_payload.get("member", {})
    best_addr = pick_best_address_info(member_payload, address)
    if not best_addr:
        raise Exception(f"找不到對應地址資料：{address}（地址需與會員留存地址一致，或請手動輸入完整地址重新比對）")

    selected_address = str(best_addr.get("address") or address).strip()

    geo_lat, geo_lng = geocode_address(selected_address)
    if geo_lat and geo_lng:
        best_addr["lat"] = geo_lat
        best_addr["lng"] = geo_lng

    addr_check = check_contain(
        session,
        member.get("member_id", ""),
        selected_address,
        best_addr.get("lat", ""),
        best_addr.get("lng", ""),
        token,
        clean_type_id,
    )
    if not addr_check and lookup_result.get("token") and lookup_result.get("token") != token:
        addr_check = check_contain(
            session,
            member.get("member_id", ""),
            selected_address,
            best_addr.get("lat", ""),
            best_addr.get("lng", ""),
            lookup_result["token"],
            clean_type_id,
        )
    if not addr_check:
        route = BOOKING_ENDPOINT_MAP.get(payway, "/booking/single")
        raise Exception(f"查詢地址/地區失敗（{payway}：{route}）：{selected_address}")

    area_info = addr_check.get("area") if isinstance(addr_check.get("area"), dict) else {}
    if area_info:
        best_addr["area_id"] = area_info.get("area_id", best_addr.get("area_id"))
        best_addr["company_id"] = area_info.get("company_id", best_addr.get("company_id"))
        best_addr["country_id"] = area_info.get("country_id", best_addr.get("country_id"))

    purchase_info = addr_check.get("purchase") if isinstance(addr_check.get("purchase"), dict) else {}
    fare_from_check = first_nonzero(
        purchase_info.get("fare") if purchase_info else "",
        purchase_info.get("car_fare") if purchase_info else "",
        purchase_info.get("traffic_fee") if purchase_info else "",
        area_info.get("fare") if area_info else "",
        area_info.get("car_fare") if area_info else "",
        area_info.get("traffic_fee") if area_info else "",
        find_nested_value(addr_check, ["fare", "car_fare", "traffic_fee", "trafficFee", "車馬費"]),
        best_addr.get("fare", ""),
        default=str(fallback_fare or "0"),
    )
    best_addr["fare"] = fare_from_check

    # 發票欄位：若呼叫端有明確指定，優先使用；否則沿用地址/會員前次資料。
    # invoice_type: 2=二聯式, 3=三聯式；carrier_type_id: 1=會員載具(email), 2=手機條碼。
    invoice_type = str(
        first_nonzero(
            invoice_type_override,
            purchase_info.get("invoiceType") if purchase_info else "",
            find_nested_value(addr_check, ["invoiceType", "invoice_type"]),
            default="2",
        )
    )
    carrier_type_id = str(
        first_nonzero(
            carrier_type_id_override,
            purchase_info.get("carrierTypeId") if purchase_info else "",
            default="1",
        )
    )
    carrier_info = str(
        first_nonzero(
            carrier_info,
            purchase_info.get("carrierInfo") if purchase_info else "",
            member.get("email") or "",
            default="",
        )
    )
    company_title = str(
        first_nonzero(company_title, purchase_info.get("companyTitle", "") if purchase_info else "", default="")
    )
    company_no = str(
        first_nonzero(company_no, purchase_info.get("companyNo", "") if purchase_info else "", default="")
    )
    donate_code = str(purchase_info.get("donateCode", "8585") if purchase_info else "8585")

    old_purchase = best_addr.get("purchase", {}) if isinstance(best_addr.get("purchase"), dict) else {}

    def pick(key, default=""):
        value = old_purchase.get(key)
        return value if value not in (None, "") else default

    base_data = {
        "clean_type_id": clean_type_id,
        "phone": phone,
        "name": str(member.get("name") or "").strip(),
        "email": str(member.get("email") or "").strip(),
        "tel": str(member.get("tel") or phone),
        "line": str(member.get("line") or ""),
        "fbName": str(member.get("fb_name") or ""),
        "fb": str(member.get("fb") or ""),
        "memoProcess": str(member.get("memo_process") or ""),
        "memoFinance": str(member.get("memo_finance") or ""),
        "addressId": str(best_addr.get("addressId") or ""),
        "country_id": str(best_addr.get("country_id") or pick("country_id", "12")),
        "address": selected_address,
        "ping": str(pick("ping", "4")),
        "room": str(pick("room", "0")),
        "bathroom": str(pick("bathroom", "0")),
        "balcony": str(pick("balcony", "0")),
        "livingroom": str(pick("livingroom", "0")),
        "kitchen": str(pick("kitchen", "0")),
        "window": str(pick("window", "")),
        "shutter": str(pick("shutter", "")),
        "clothes": str(pick("clothes", "0")),
        "dyson": str(pick("dyson", "0")),
        "refrigerator": str(pick("refrigerator", "0")),
        "disinfection": str(pick("disinfection", "0")),
        "go_abord": str(pick("go_abord", "0")),
        "home_move": str(pick("home_move", "0")),
        "storage": str(pick("storage", "0")),
        "cabinet": str(pick("cabinet", "0")),
        "quintuple": str(pick("quintuple", "0")),
        "hour": str(int(float(hour))),
        "price": "",
        "price_vvip": "",
        "person": str(person),
        "date_s": date_s,
        "period_s": period_s,
        "period": "",
        "cycle": "1",
        "fare": "",
        "memo": "",
        "notice": str(best_addr.get("notice") or old_purchase.get("notice") or ""),
        "discount_code": str(discount_code or ""),
        "payment": str(payment_type or ""),
        "carrierInfo": str(carrier_info or ""),
        "companyNo": str(company_no or ""),
        "companyTitle": str(company_title or ""),
        "payway": PAYWAY_MAP.get(payway, "2"),
        "invoice_type": invoice_type,
        "carrier_type_id": carrier_type_id,
        "carrier_info": carrier_info,
        "company_title": company_title,
        "company_no": company_no,
        "donate_code": donate_code,
        "is_backend": "477",
        "member_id": str(member.get("member_id") or ""),
        "company_id": str(best_addr.get("company_id") or pick("company_id", "1")),
        "area_id": str(best_addr.get("area_id") or pick("area_id", "25")),
        "lat": str(best_addr.get("lat") or pick("lat", "")),
        "lng": str(best_addr.get("lng") or pick("lng", "")),
    }

    calc_result = calculate_hour(session, base_data, token)
    if not calc_result:
        raise Exception("計算時數失敗")

    calc_fields = extract_calc_fields(
        calc_result,
        fallback_hours=base_data["hour"],
        fallback_fare=best_addr.get("fare", "0"),
    )
    base_data["price"] = str(calc_fields.get("price") or "0")
    base_data["price_vvip"] = str(calc_fields.get("price_vvip") or "0")
    base_data["fare"] = first_nonzero(calc_fields.get("fare"), best_addr.get("fare"), default="0")

    if base_data["price"] in ("", "0", "0.0") and payway != "儲值金":
        raise Exception("計算時數後金額為 0，請確認坪數/時數設定是否正確")

    slot = f"{date_s}_{period_s}"
    raw_section = get_section_raw(session, base_data, token, slot)
    if not slot_exists_in_section_response(raw_section, slot):
        raise Exception(f"該時段無班表：{slot}")

    cleaners = extract_cleaners_from_section_response(raw_section, slot)
    staff_display = format_staff_from_cleaners(cleaners, people=person)

    booking_url = _booking_url_for_payway(base_url, payway)

    before_order_nos = list_order_numbers_for_phone(session, phone, name=member_name)

    booking_resp = session.post(
        booking_url,
        data=_build_booking_submit_data(base_data, token, payway, slot),
        headers=HEADERS,
        allow_redirects=True,
    )

    display_period = display_period_text(period_s.split("-")[0], period_s.split("-")[1])

    # v7.6: /booking/stored_value_routine 在部分環境會回傳 {"count":1}，
    # 代表後台已受理，但訂單列表可能延遲更新；因此改為多次回查新訂單。
    after_order_nos = set()
    new_order_nos = set()
    for wait_seconds in (1, 2, 3, 5):
        time.sleep(wait_seconds)
        after_order_nos = list_order_numbers_for_phone(session, phone, name=member_name)
        new_order_nos = after_order_nos - before_order_nos
        if new_order_nos:
            break

    def _booking_count_success(resp):
        try:
            payload = resp.json()
        except Exception:
            return False
        try:
            return int(payload.get("count", 0)) > 0
        except Exception:
            return False

    def _find_matching_order_after_submit():
        """後台回 count=1 但列表差集抓不到時，依本次建單條件回查最可能的訂單。"""
        blocks = _fetch_purchase_blocks_for_phone(session, phone, name=member_name)
        target_addr_norm = normalize_addr_for_match(selected_address)
        target_period_compact = str(period_s or "").replace(" ", "")
        target_display_compact = str(display_period or "").replace(" ", "")
        matched = []
        for block in blocks:
            order_no_candidate = block.get("order_no")
            if not order_no_candidate:
                continue
            lines = block.get("lines", [])
            joined = "\n".join(lines)
            service_date_found, service_time_found = _parse_service_date_time_loose(joined)
            if service_date_found != date_s:
                continue
            joined_addr_norm = normalize_addr_for_match(joined)
            if target_addr_norm and target_addr_norm not in joined_addr_norm:
                continue
            payway_found = _extract_payway_line(joined)
            if payway_found and payway_found != payway:
                continue
            time_compact = str(service_time_found or "").replace(" ", "")
            if target_period_compact and target_period_compact not in time_compact and target_display_compact not in time_compact:
                continue
            matched.append(order_no_candidate)

        # 優先選擇 before 沒看過的；如果後台列表排序/篩選造成差集失敗，再取最新排序第一筆。
        for candidate in matched:
            if candidate not in before_order_nos:
                return candidate
        return matched[0] if matched else None

    if not new_order_nos:
        order_no = _find_matching_order_after_submit() if _booking_count_success(booking_resp) else None
        if not order_no:
            debug_snippet = booking_resp.text[:300].replace("\n", " ").strip()
            extra_hint = "後台回傳 count > 0，但訂單列表回查不到符合條件的新訂單；請檢查訂單管理是否已建立。" if _booking_count_success(booking_resp) else ""
            raise Exception(
                "建單失敗：系統未產生新訂單編號（可能該客人此時段已有訂單存在、後台拒絕重複預約，"
                "或表單缺少必填欄位導致後台驗證沒過）。請至後台『訂單管理』手動確認，不要直接使用畫面上顯示的舊訂單資訊。\n"
                f"{extra_hint}\n"
                f"［除錯資訊］回應狀態：{booking_resp.status_code}，回應網址：{booking_resp.url}\n"
                f"回應片段：{debug_snippet}"
            )
    elif len(new_order_nos) == 1:
        order_no = next(iter(new_order_nos))
    else:
        order_no = None
        for candidate in new_order_nos:
            meta = fetch_order_meta_by_order_no(session, candidate)
            if meta.get("服務日期") == date_s and display_period.replace(" ", "") in str(meta.get("服務時間", "")).replace(" ", ""):
                order_no = candidate
                break
        if not order_no:
            order_no = sorted(new_order_nos)[-1]

    meta = fetch_order_meta_by_order_no(session, order_no)

    price_no_tax = base_data["price"]
    try:
        price_with_tax = int(round(float(price_no_tax) * TAX_RATE))
    except Exception:
        price_with_tax = price_no_tax

    return {
        "order_no": order_no,
        "address": selected_address,
        "date": date_s,
        "period": display_period,
        "period_s": period_s,            # v7.3: 供 _format_period_display() 用
        "person": str(person),           # v7.3: 供 _format_period_display() 用
        "price": price_no_tax,
        "price_with_tax": price_with_tax,
        "service_amount": price_with_tax,
        "fare": base_data["fare"],
        "payway": payway,
        "region": region,
        "staff": meta.get("服務人員") or staff_display,
        "service_status": meta.get("服務狀態", "未處理"),
        "env_name": env_name,
        "session": session,
    }


def _get_newest_coupon_code(session, base_url, prefix):
    """
    建立優惠券後，取「最新建立的那張」的實際優惠碼。

    做法：
    1. GET /coupon 列表，抓第一筆（剛建的那張）的 detail ID
    2. GET /coupon/detail/{id}，從頁面抓 prefix 開頭的優惠碼

    若抓不到，回傳 prefix（讓客服自行至後台確認）。
    """
    try:
        list_resp = session.get(f"{base_url}/coupon", headers=HEADERS, allow_redirects=True)
        if list_resp.status_code != 200:
            return prefix

        ids = re.findall(r"/coupon/detail/(\d+)", list_resp.text)
        if not ids:
            return prefix

        # 只看第一筆（最新）
        detail_resp = session.get(f"{base_url}/coupon/detail/{ids[0]}", headers=HEADERS)
        if detail_resp.status_code != 200:
            return prefix

        prefix_esc = re.escape(prefix)
        codes = re.findall(rf"\b{prefix_esc}[A-Za-z0-9]*\b", detail_resp.text)
        # 過濾掉只剩前綴本身（要有後綴字母）
        codes = [c for c in codes if len(c) > len(prefix)]
        return codes[0] if codes else prefix
    except Exception:
        return prefix

def _fetch_order_edit_id(session, order_no):
    """
    從訂單查詢頁面抓取該訂單的後台編輯 ID（/purchase/edit/{id} 裡的數字 ID）。
    """
    params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
    params["orderNo"] = str(order_no).strip()
    resp = session.get(orders.PURCHASE_URL, params=params, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return None
    m = re.search(r"/purchase/edit/(\d+)", resp.text)
    return m.group(1) if m else None


def _update_order_note(session, base_url, order_no, note):
    """
    更新訂單的「客服備註」（memoProcess 欄位）。

    流程：
    1. 查訂單取得後台編輯 ID
    2. GET 編輯頁面取 CSRF token 與現有表單值
    3. POST 更新（保留其他欄位，只改 memoProcess）

    回傳 (成功bool, 訊息str)。
    """
    try:
        edit_id = _fetch_order_edit_id(session, order_no)
        if not edit_id:
            return False, f"找不到訂單 {order_no} 的編輯 ID"

        edit_url = f"{base_url}/purchase/edit/{edit_id}"
        get_resp = session.get(edit_url, headers=HEADERS, allow_redirects=True)
        if get_resp.status_code != 200:
            return False, f"無法開啟編輯頁面：HTTP {get_resp.status_code}"

        # 取 CSRF token
        token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', get_resp.text)
        csrf = token_m.group(1) if token_m else ""
        if not csrf:
            return False, "無法取得 CSRF token"

        # 從現有頁面抓所有 input/textarea 值，避免 POST 時清空其他欄位
        existing = {}
        for m2 in re.finditer(
            r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"[^>]*>', get_resp.text
        ):
            existing[m2.group(1)] = m2.group(2)
        for m2 in re.finditer(
            r'<textarea[^>]+name="([^"]+)"[^>]*>([^<]*)</textarea>', get_resp.text
        ):
            existing[m2.group(1)] = m2.group(2).strip()

        existing["_token"] = csrf
        existing["_method"] = "PUT"
        existing["memoProcess"] = note  # 客服備註欄位

        post_resp = session.post(
            edit_url,
            data=existing,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True,
        )
        success = post_resp.status_code in (200, 302)
        return success, f"HTTP {post_resp.status_code}"
    except Exception as e:
        return False, str(e)


def convert_order(
    env_name,
    backend_email,
    backend_password,
    order_no_a,
    new_person,
    new_hour,
    new_date_s,
    new_period_s,
    clean_type_id="1",
):
    """
    訂單轉換：原訂單 A → 建折價券 → 建新訂單 B。

    流程：
    1. 查原訂單 A：取金額/日期/電話/地址/付款方式/區域
    2. 建折價券（面額=A金額，有效期=今天到A服務日，prefix=convXXXX）
    3. 從後台確認實際優惠碼
    4. 查會員（A的電話）→ 建新訂單 B（套折價券）
    5. 回傳備註文字 + 操作指引（勾檸檬人需手動）

    回傳 dict：
        order_no_a / order_no_b / coupon_code / note_a / note_b
        edit_url_a（原訂單A後台連結，用來手動勾檸檬人）
        line_message（新訂單B的 LINE 通知）
    """
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    # ── Step 1: 查原訂單 A ──────────────────────────────────────────
    block_a = _fetch_purchase_block_for_order_no(session, order_no_a)
    lines_a = block_a.get("lines", [])
    joined_a = "\n".join(lines_a)

    service_date_a, _ = _parse_service_date_time_loose(joined_a)
    address_a = _extract_address_line(lines_a)
    payway_a = _extract_payway_line(joined_a)
    fare_a = _extract_fare_line(joined_a) or "0"
    service_amount_a = _service_amount_from_block(joined_a, fare_a)
    phone_a = _extract_phone_from_block_lines(lines_a)
    region_a = get_region_by_address(address_a, ACCOUNTS) or "台北"

    if not phone_a:
        raise Exception(f"無法從訂單 {order_no_a} 取得客人電話，請手動確認")
    if not service_amount_a or service_amount_a == "0":
        raise Exception(f"訂單 {order_no_a} 金額為 0 或無法取得，請確認訂單付款狀態")
    if not service_date_a:
        raise Exception(f"訂單 {order_no_a} 無法取得服務日期")
    if not address_a:
        raise Exception(f"訂單 {order_no_a} 無法取得服務地址")

    # ── Step 2: 建折價券 ────────────────────────────────────────────
    today_str = date.today().strftime("%Y-%m-%d")
    coupon_prefix = order_no_a[-4:]  # e.g., 3121
    coupon_discount = int(float(str(service_amount_a).replace(",", "")))

    # 取 CSRF token（GET /coupon/add）
    coupon_add_url = f"{base_url}/coupon/add"
    get_resp = session.get(coupon_add_url, headers=HEADERS, allow_redirects=True)
    if get_resp.status_code != 200:
        raise Exception("無法開啟優惠券新增頁面")
    token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', get_resp.text)
    csrf = token_m.group(1) if token_m else ""
    if not csrf:
        raise Exception("無法取得 CSRF token")

    coupon_fields = [
        ("coupon_type_id", "1"),
        ("title", f"訂單轉換-{order_no_a}"),
        ("date_s", today_str),
        ("date_e", service_date_a),
        ("prefix", coupon_prefix),
        ("discount", str(coupon_discount)),
        ("piece", "2"),
        ("_token", csrf),
    ]
    # 限制地區：台北、台中
    for region_name in ["台北", "台中"]:
        coupon_fields.append(("company_id[]", COUPON_COMPANY_ID_MAP[region_name]))
    # 限制服務：居家清潔、裝修細清
    for svc_name in ["居家清潔", "裝修細清"]:
        coupon_fields.append(("service_item[]", COUPON_SERVICE_ITEM_MAP[svc_name]))
    # multipart/form-data（list of tuples 保留重複 key）
    coupon_files = [(k, (None, v)) for k, v in coupon_fields]
    post_headers = {k: v for k, v in HEADERS.items() if k.lower() != "content-type"}
    coupon_resp = session.post(
        coupon_add_url,
        files=coupon_files,
        headers=post_headers,
        allow_redirects=True,
    )
    # 後台建券成功後 redirect 可能回 500，但券實際上已建立
    # 不依賴 status_code，直接去找剛建的那張優惠碼
    time.sleep(1)
    coupon_code = _get_newest_coupon_code(session, base_url, coupon_prefix)
    if not coupon_code or coupon_code == coupon_prefix:
        # 真的沒建到才報錯
        err_text = coupon_resp.text
        title_m = re.search(r"<title>([^<]+)</title>", err_text)
        detail = title_m.group(1)[:100] if title_m else f"HTTP {coupon_resp.status_code}"
        raise Exception(f"折價券建立失敗，請至後台確認：{detail}")

    # ── Step 4: 查會員 → 建新訂單 B ─────────────────────────────────
    token_booking = get_csrf_token(session)
    member_payload = get_member(session, phone_a, token_booking, clean_type_id)
    if not member_payload:
        raise Exception(f"電話 {phone_a} 查無會員資料")

    lookup_result = {
        "session": session,
        "token": token_booking,
        "phone": phone_a,
        "member_payload": member_payload,
        "base_url": base_url,
        "env_name": env_name,
    }

    order_b_result = quick_create_order(
        env_name=env_name,
        payway=payway_a,
        region=region_a,
        lookup_result=lookup_result,
        address=address_a,
        clean_type_id=clean_type_id,
        date_s=new_date_s,
        period_s=new_period_s,
        hour=new_hour,
        person=new_person,
        discount_code=coupon_code,
    )
    order_no_b = order_b_result["order_no"]

    # ── Step 5: 備註文字 + 自動寫入 ────────────────────────────────
    combo_desc = f"{new_person}人{new_hour}小時"
    note_b = f"{order_no_a}+{order_no_b} 合併{combo_desc}服務"
    note_a = f"{order_no_a}+{order_no_b} 合併{combo_desc}服務，檸檬人勿動"

    # 自動寫備註（失敗不阻斷主流程，錯誤訊息回傳供畫面顯示）
    note_a_ok, note_a_msg = _update_order_note(session, base_url, order_no_a, note_a)
    note_b_ok, note_b_msg = _update_order_note(session, base_url, order_no_b, note_b)

    # 新訂單 B 的 LINE 訊息
    line_msg = build_line_message(order_b_result)

    # ── Step 6: 自動勾檸檬人 ─────────────────────────────────────────
    lemon_result = assign_lemon_cleaners_to_order(
        session=session,
        base_url=base_url,
        order_no_a=order_no_a,
        service_date=service_date_a,
        period_s=new_period_s,
        person_count=new_person,
    )

    return {
        "order_no_a": order_no_a,
        "order_no_b": order_no_b,
        "coupon_code": coupon_code,
        "lemon_result": lemon_result,
        "coupon_discount": coupon_discount,
        "service_date_a": service_date_a,
        "combo_desc": combo_desc,
        "note_a": note_a,
        "note_b": note_b,
        "note_a_ok": note_a_ok,
        "note_a_msg": note_a_msg,
        "note_b_ok": note_b_ok,
        "note_b_msg": note_b_msg,
        "edit_url_a": f"{base_url}/purchase/edit/{order_no_a.replace('LC00', '')}",
        "purchase_url_a": f"{base_url}/purchase?orderNo={order_no_a}",
        "line_message": line_msg,
        "order_b_result": order_b_result,
        "region": region_a,
    }



# =========================================================
# 勾檸檬人（訂單轉換用）
# =========================================================

# 時段衝突表：原訂單時段 → 哪些班表時段會衝突
# 班表頁 radio value → 班表代碼
VALUE_TO_SHIFT_CODE = {
    "6":         "全6",
    "8":         "全8",
    "0830-1230": "上4",
    "0900-1200": "上3",
    "0900-1100": "上2",
    "1400-1600": "下2",
    "1400-1700": "下3",
    "1400-1800": "下4",
    "1900-2100": "晚2",
}

SHIFT_CONFLICT_TABLE = {
    "全6": {"上3", "上4", "上2", "全6", "全8"},
    "全8": {"上3", "上4", "上2", "下2", "下3", "下4", "全6", "全8"},
    "上3": {"上3", "上4", "上2", "全6", "全8"},
    "上4": {"上3", "上4", "上2", "全6", "全8"},
    "上2": {"上3", "上4", "上2", "全6", "全8"},
    "下3": {"下2", "下3", "下4", "全6", "全8"},
    "下4": {"下2", "下3", "下4", "全6", "全8"},
    "下2": {"下2", "下3", "下4", "全6", "全8"},
}

# 時段代碼 → 標準時間 mapping（用來從訂單時段推出代碼）
PERIOD_TO_SHIFT_CODE = {
    "09:00-12:00": "上3",
    "08:30-12:30": "上4",
    "09:00-11:00": "上2",
    "14:00-16:00": "下2",
    "14:00-17:00": "下3",
    "14:00-18:00": "下4",
    "09:00-16:00": "全6",
    "09:00-18:00": "全8",
}


def _period_to_shift_code(period_s):
    """將訂單時段字串轉為班表代碼，例如 '09:00-16:00' → '全6'。"""
    compact = str(period_s or "").replace(" ", "")
    return PERIOD_TO_SHIFT_CODE.get(compact, "")


def _search_lemon_cleaners(session, base_url):
    """
    GET /cleaner1?keyword=檸檬
    解析 /cleaner1/{id}/shift 連結，取得所有檸檬人的 (id, name) 列表。
    HTML 格式：<a href="/cleaner1/28/shift">排班</a> 附近有「檸檬人1」文字。
    """
    resp = session.get(
        f"{base_url}/cleaner1",
        params={"keyword": "檸檬"},
        headers=HEADERS,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        return []

    entries = []
    seen_ids = set()
    # 找 /cleaner1/{id}/shift 連結，取其附近的檸檬人名稱
    for m in re.finditer(r'/cleaner1/(\d+)/shift', resp.text):
        cid = m.group(1)
        if cid in seen_ids:
            continue
        # 取該連結前後 300 字找名稱
        ctx_start = max(0, m.start() - 300)
        ctx = resp.text[ctx_start: m.end() + 100]
        name_m = re.search(r"檸檬人\d+", ctx)
        if name_m:
            seen_ids.add(cid)
            entries.append((cid, name_m.group(0)))

    # 備用：從詳細資料連結抓
    if not entries:
        for m in re.finditer(r'/cleaner1/(\d+)\"[^>]*>詳細資料', resp.text):
            cid = m.group(1)
            if cid in seen_ids:
                continue
            ctx = resp.text[max(0, m.start()-300): m.end()+50]
            name_m = re.search(r"檸檬人\d+", ctx)
            name = name_m.group(0) if name_m else f"檸檬人"
            seen_ids.add(cid)
            entries.append((cid, name))

    return entries


def _get_cleaner_shifts_on_date(session, base_url, cleaner_id, date_str):
    """
    GET /cleaner1/{id}/shift?month=YYYY-MM → 回傳指定日期已勾選的班表代碼 set。
    例如：{"上3", "全6"}
    """
    ym = date_str[:7]  # "2026-07"
    resp = session.get(
        f"{base_url}/cleaner1/{cleaner_id}/shift",
        params={"month": ym},
        headers=HEADERS,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        return set()

    # 找目標日期區塊
    # 後台格式通常是 data-date="2026-07-20" 或日期出現在 <th>
    day = date_str[8:10].lstrip("0")  # "20"
    # 嘗試幾種格式找日期區塊
    patterns = [
        rf'{re.escape(date_str)}.*?(?={re.escape(ym)}|$)',
        rf'>{day}</.*?(?=>\d{{1,2}}</|$)',
    ]
    # 更可靠：找 isLock checkbox 帶有日期
    checked = set()
    checked = set()
    # 找 isLock checkbox 帶有日期（格式：name="isLock[DATE][TYPE]" checked）
    day_pat = date_str.replace("-", "\\-")
    for m in re.finditer(
        r'isLock\[(' + re.escape(date_str) + r'\]\[([^\]]+)\][^>]*checked',
        resp.text, re.I,
    ):
        checked.add(m.group(2))

    # 備用：value="YYYY-MM-DD_TYPE" checked
    if not checked:
        for m in re.finditer(
            re.escape(date_str) + r'_([A-Za-z0-9]+)[^>]*checked',
            resp.text, re.I,
        ):
            checked.add(m.group(1))

def _get_schedule_edit_info(session, base_url, date_str, purchase_id):
    """
    GET /schedule/edit?date={date}&purchase_id={id}
    解析：
    - originShiftId[] 值（目前配班的 shift ID）
    - 各槽位可換班的 {名稱: shiftId} dict
    回傳 (csrf, origin_ids, slots)
    slots = [{name: shift_id, ...}, ...]  一個 dict per 人員槽位
    """
    resp = session.get(
        f"{base_url}/schedule/edit",
        params={"date": date_str, "purchase_id": purchase_id},
        headers=HEADERS,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        return None, [], []

    token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', resp.text)
    csrf = token_m.group(1) if token_m else ""

    origin_ids = re.findall(r'name=["\']originShiftId\[\]["\'][^>]*value=["\']?(\d+)["\']?', resp.text)
    if not origin_ids:
        origin_ids = re.findall(r'value=["\']?(\d+)["\'][^>]*name=["\']originShiftId\[\]', resp.text)

    # 解析各槽位可換班人員
    # HTML 格式：<label for="shift_N_SHIFTID">姓名</label>
    #            <input type="radio" name="shiftId[N]" value="SHIFTID">
    slots = []
    slot_blocks = re.split(r'name=["\']originShiftId\[\]', resp.text)[1:]
    for block in slot_blocks:
        # 取到下一個 originShiftId 為止
        # 找所有 label/input 對
        slot_map = {}
        for m in re.finditer(
            r'<label[^>]+for=["\']shift_\d+_(\d+)["\'][^>]*>([^<]+)</label>',
            block,
        ):
            shift_id = m.group(1)
            name = m.group(2).strip()
            slot_map[name] = shift_id
        slots.append(slot_map)

    return csrf, origin_ids, slots


def assign_lemon_cleaners_to_order(session, base_url, order_no_a, service_date, period_s, person_count):
    """
    自動把原訂單A的配班人員換成檸檬人。

    關鍵做法：直接到 /schedule/edit 頁面找可換班清單中含「檸檬人」的選項，
    後台已自動過濾真正有訂單衝突的人，不需要另外查班表。

    Steps:
    1. 從訂單列表抓 purchase_id（後台內部 ID）
    2. GET /schedule/edit?date=…&purchase_id=… 取排班修改頁
    3. 解析各槽位可換班清單，找含「檸檬人」的 radio
    4. 選前 N 個（N = person_count）
    5. POST 送出
    """
    # Step 1: 取 purchase_id
    purchase_id = _fetch_order_edit_id(session, order_no_a)
    if not purchase_id:
        return {"success": False, "message": f"無法取得訂單 {order_no_a} 的後台 ID"}

    # Step 2: GET 排班修改頁
    csrf, origin_ids, slots = _get_schedule_edit_info(
        session, base_url, service_date, purchase_id
    )
    if not slots:
        return {"success": False, "message": "無法取得排班修改頁，請手動操作"}

    # Step 3-4: 各槽位找含「檸檬人」的選項
    n = int(person_count)
    shift_choices = []
    assigned_names = []
    missing_slots = []

    for i, slot_map in enumerate(slots):
        if i >= n:
            break
        # slot_map 是 {名稱: shiftId}，找含「檸檬人」的那個
        lemon_entry = None
        for name_key, sid in slot_map.items():
            if "檸檬人" in name_key:
                lemon_entry = (name_key.strip(), sid)
                break
        if lemon_entry:
            shift_choices.append(lemon_entry[1])
            assigned_names.append(lemon_entry[0])
        else:
            missing_slots.append(i + 1)

    if missing_slots:
        available = list(slots[0].keys()) if slots else []
        return {
            "success": False,
            "message": f"槽位 {missing_slots} 找不到可用的檸檬人（排班頁未顯示）",
            "available_in_slot0": available[:10],
        }

    # Step 5: POST
    fields = [("_token", csrf), ("_method", "PUT")]
    for oid in origin_ids:
        fields.append(("originShiftId[]", oid))
    for j, sid in enumerate(shift_choices):
        fields.append((f"shiftId[{j}]", sid))

    post_resp = session.post(
        f"{base_url}/schedule/edit",
        params={"date": service_date, "purchase_id": purchase_id},
        data=fields,
        headers=HEADERS,
        allow_redirects=True,
    )
    success = post_resp.status_code in (200, 302)
    return {
        "success": success,
        "assigned": assigned_names,
        "message": f"已將配班改為：{'、'.join(assigned_names)}" if success
                   else f"POST 失敗：HTTP {post_resp.status_code}",
    }


def send_confirmation(order_result):
    """送出後寄確認信，回傳 (是否成功, 訊息)"""
    session = order_result["session"]
    order_no = order_result["order_no"]
    return send_confirmation_mail(session, order_no)


def build_line_message(order_result):
    """
    依訂單的 payway + region 自動挑選對應的 LINE 通知樣板。
    回傳純文字，方便前端直接複製貼上 LINE。

    金額一律使用含稅金額（price_with_tax）。
    服務時間格式：HH:MM-HH:MM(X小時[,中間休息1小時])-N人
    """
    payway = order_result["payway"]
    region = order_result["region"]
    date_disp = order_result["date"].replace("-", "/")

    # v7.5: 同日多筆時用 combined_period（已含完整人數/時數/休息說明）
    # v7.4: 單筆時若有 actual_period（簡訊實際服務時間），覆蓋顯示時間
    # v7.3: 一般單筆用 period_s 查 PERIOD_DISPLAY_INFO 格式化
    combined_period = str(order_result.get("combined_period", "") or "")
    if combined_period:
        period = combined_period
    else:
        period_raw = str(
            order_result.get("period_s") or order_result.get("period", "")
        ).replace(" ", "")
        actual_period = str(order_result.get("actual_period", "") or "")
        person_cnt = str(order_result.get("person", "") or "")
        period = _format_period_display(period_raw, person_cnt, display_override=actual_period)

    price = order_result.get("service_amount") or order_result.get("price_with_tax", order_result.get("price"))
    fare = order_result["fare"]
    address = order_result["address"]
    order_no = order_result["order_no"]
    # 多日期合併時，combined_period 已含各自日期，不再加 date_disp 前綴
    multi_date = order_result.get("multi_date", False)
    if multi_date and combined_period:
        service_time_line = f"服務時間 :\n{period}"
    else:
        service_time_line = f"服務時間 : {date_disp}  {period}"
    order_last6 = order_no[-6:] if len(order_no) >= 6 else order_no
    try:
        has_fare = float(str(fare or "0").replace(",", "")) != 0
    except Exception:
        has_fare = bool(str(fare or "").strip())
    vip_fare_line = f"車馬費：{fare}\n" if has_fare else ""
    card_fare_line = f"車馬費： {fare}   (服務完後收取)\n" if has_fare else ""
    taipei_atm_fare_line = f"車馬費：{fare}\n" if has_fare else ""
    taichung_atm_fare_line = f"\n車馬費:{fare}" if has_fare else ""

    common_footer = """**當您完成付款後即表示服務已完成預約，
預約完成後，即代表您同意接受檸檬專業清潔公司 服務條款 及 隱私權政策。
請詳閱服務條款及隱私權相關說明 https://www.lemonclean.com.tw/terms
＊若現場溝通時確認無法於服務時間內完成服務需求，會請您排優先順序，以時間內可以完成的區域為主。
＊窗戶獨立於各區域單獨計算，拆紗窗不拆玻璃，含窗溝及窗框及內側，若外側無法安全站立則以手能擦拭範圍為主。
＊夏季天氣炎熱，若情況充許請提供電扇或冷氣供專員使用，謝謝。
＊若超過服務時間，則會以加時費用計算。
若訂購後有上述情事請主動連繫檸檬家事官方LINE@，謝謝。"""

    cancel_policy = """**異動/取消服務注意事項
凡訂單成立付款後，若異動日期或取消服務異動手續費如下
 **工作日不含例假日且以上班時間計之，超過 17:30 算下個工作日。
◎服務日3個工作天前，取消酌收訂單5%手續費。
◎服務日2-3個工作天內，取消或更改酌收訂單30%手續費。
◎服務日1個工作天內，取消或更改酌收訂單50%手續費。"""

    if payway == "儲值金":
        return f"""感謝您預約檸檬家事【居家清潔】服務
服務時間：{date_disp} {period}
服務地址：{address}

{vip_fare_line}檸檬家事專員會於現場再溝通服務需求，
以於系統估算時間內可以完的服務項目為主。
預約完成後，即代表您同意接受檸檬專業清潔公司 服務條款 及 隱私權政策。
請詳閱服務條款及隱私權相關說明 https://www.lemonclean.com.tw/terms

建議您可以至會員中心》訂單查詢 確認喔
https://www.lemonclean.com.tw/login
帳號：email；密碼：手機號碼
＊即日起本站暫停做防疫調查，為保障客戶及專員安全，若確診請於服務前日主動告知，否則需付異動費喔
若訂購後有上述情事請主動連繫檸檬家事官方LINE@，謝謝。

VIP客戶
◎異動費
VIP若取消/異動服務日期，需於服務日前4個工作天上班時間(不含例假日，17:30後算下個工作日)告知。
若於服務前2-3個工作日告知，則收取每2人1小時異動費200元；
若於服務前1個工日(含服務當天)告知，則收取每2人1小時異動費300元。"""

    if payway == "信用卡":
        # 合併單時產生多個付款連結，每筆訂單各一條
        all_order_nos = order_result.get("all_order_nos") or [order_no]
        if len(all_order_nos) > 1:
            link_lines = []
            for idx, ono in enumerate(all_order_nos, start=1):
                last6 = ono[-6:] if len(ono) >= 6 else ono
                link_lines.append(f"訂單{idx}：https://www.lemonclean.com.tw/order/{last6}")
            payment_links = "\n".join(link_lines)
        else:
            payment_links = f"https://www.lemonclean.com.tw/order/{order_last6}"

        return f"""感謝您於 檸檬家事 預約【居家清潔】服務！
{service_time_line}
服務金額：{price}（含稅）
{card_fare_line}服務地址：{address}
※麻煩您於『明天 24:00前』完成付款，為保留他人訂購權利，逾期付款訂單將自動取消

{common_footer}

線上刷卡流程:
{payment_links}
登入會員
帳號：email；密碼：手機號碼
在訂單點選付款狀態點選『重新付款』即可

{cancel_policy}"""

    if payway == "ATM":
        if region == "台北":
            bank_block = """銀行戶名：檸檬專業清潔有限公司
銀行代碼 台北富邦銀行(012)-松高分行
銀行帳號 7091-2000-3320"""
            extra_note = """*發票於付款完成後24小時之內會開立並寄至Email，屆時麻煩查收或是檢查垃圾郵件。
*匯款完成後再請您提供您的匯款帳號後5碼，以供檸檬家事為您核對帳款。
"""
        else:
            bank_block = """銀行戶名：泳檬有限公司
銀行代碼 台北富邦銀行(012)-營業部
銀行帳號 00200102520512"""
            extra_note = ""

        atm_pay_title = "▲請您依下列匯款帳戶資訊繳費，謝謝！" if region == "台北" else "請您依下列匯款帳戶資訊繳費，謝謝！"
        extra_note_block = f"\n{extra_note}" if extra_note else ""
        service_lines = (
            f"{service_time_line}\n{taipei_atm_fare_line}服務地址：{address}"
            if region == "台北"
            else f"{service_time_line}\n服務地址：{address}{taichung_atm_fare_line}"
        )

        return f"""感謝您於 檸檬家事 預約【居家清潔】服務！
{service_lines}
※麻煩您於『明天 24:00前』完成付款，為保留他人訂購權利，逾期付款訂單將自動取消

{common_footer}

{atm_pay_title}
{bank_block}
轉帳金額  {price}元（含營業稅）

訂單可以登入『會員中心』查詢確認
https://www.lemonclean.com.tw/login
帳號：email；密碼：手機號碼
{extra_note_block}
{cancel_policy}"""

    raise Exception(f"未知付款方式: {payway}")


# =========================================================
# 需求搜尋 / 新客建單輔助功能（服務訂單系統 UI 使用）
# =========================================================

def _is_target_day(d, day_type="不限"):
    """依平日/週末/不限判斷日期是否符合搜尋條件。"""
    weekday = d.weekday()
    if day_type == "平日":
        return weekday < 5
    if day_type == "週末":
        return weekday >= 5
    return True


def _filter_periods_by_preference(periods, time_preference="不限"):
    """依上午/下午/不限篩選系統時段。"""
    selected = []
    for period in periods or []:
        try:
            start_hour = int(str(period).split("-", 1)[0].split(":", 1)[0])
        except Exception:
            start_hour = 0
        if time_preference == "上午" and start_hour >= 12:
            continue
        if time_preference == "下午" and start_hour < 12:
            continue
        selected.append(period)
    return selected


def build_equivalent_plans(person, hour):
    """
    產生等效人時方案。
    人時 = 人數 × 服務時數。
    """
    try:
        base_person = int(person)
        base_hour = int(float(hour))
    except Exception:
        base_person, base_hour = 2, 4

    total_person_hours = base_person * base_hour
    candidates = [(base_person, base_hour)]

    for p in range(1, 5):
        if p == base_person:
            continue
        if total_person_hours % p != 0:
            continue
        h = total_person_hours // p
        if 2 <= h <= 8:
            candidates.append((p, h))

    seen = set()
    plans = []
    for p, h in candidates:
        key = (p, h)
        if key in seen:
            continue
        seen.add(key)
        plans.append({"person": p, "hour": h, "total_person_hours": total_person_hours})
    return plans


def search_available_service_dates(
    env_name,
    payway,
    lookup_result,
    address,
    clean_type_id,
    start_date,
    days=30,
    day_type="不限",
    time_preference="不限",
    plans=None,
    periods=None,
    period_hours=None,
    max_results=30,
):
    """
    依客人需求往後搜尋可服務日期。
    回傳每筆包含：date / period / person / hour / staff。
    """
    if isinstance(start_date, datetime):
        cursor = start_date.date()
    elif isinstance(start_date, date):
        cursor = start_date
    else:
        cursor = datetime.strptime(str(start_date), "%Y-%m-%d").date()

    periods = periods or [
        "08:30-12:30",
        "09:00-11:00",
        "09:00-12:00",
        "14:00-16:00",
        "14:00-17:00",
        "14:00-18:00",
        "09:00-16:00",
        "09:00-18:00",
    ]
    period_hours = period_hours or {
        "08:30-12:30": 4,
        "09:00-11:00": 2,
        "09:00-12:00": 3,
        "14:00-16:00": 2,
        "14:00-17:00": 3,
        "14:00-18:00": 4,
        "09:00-16:00": 6,
        "09:00-18:00": 8,
    }
    periods = _filter_periods_by_preference(periods, time_preference)
    plans = plans or build_equivalent_plans(2, 4)

    results = []
    for offset in range(int(days)):
        d = cursor + timedelta(days=offset)
        if not _is_target_day(d, day_type):
            continue

        date_s = d.strftime("%Y-%m-%d")
        for plan in plans:
            target_hour = int(plan.get("hour") or 0)
            target_periods = [p for p in periods if int(period_hours.get(p, 0)) == target_hour]
            if not target_periods:
                continue
            rows = quick_check_available_slots(
                env_name=env_name,
                payway=payway,
                lookup_result=lookup_result,
                address=address,
                clean_type_id=clean_type_id,
                date_s=date_s,
                hour=target_hour,
                person=plan.get("person"),
                periods=target_periods,
                period_hours=period_hours,
            )
            for row in rows:
                if not row.get("available"):
                    continue
                results.append({
                    "date": date_s,
                    "period": row.get("period"),
                    "person": plan.get("person"),
                    "hour": target_hour,
                    "total_person_hours": plan.get("total_person_hours"),
                    "staff": row.get("staff", ""),
                })
                if len(results) >= int(max_results):
                    return results
    return results



# =========================================================
# 優惠券建立（/coupon/add）
# =========================================================

COUPON_COMPANY_ID_MAP = {
    "台北": "1",
    "桃園": "2",
    "新竹": "3",
    "台中": "4",
}

COUPON_SERVICE_ITEM_MAP = {
    "居家清潔": "1",
    "辦公室清潔": "2",
    "裝修細清": "3",
    "年節大掃除": "4",
    "冷氣機清潔": "5",
    "洗衣機清潔": "6",
    "沙發/床墊清潔": "7",
    "整理收納": "8",
}

COUPON_TYPE_MAP = {
    "不得與其他優惠券重複": "1",
    "可重複使用，每個帳號限用一次": "2",
    "可重複使用，不限使用次數": "3",
}

COUPON_ADD_URL_PATH = "/coupon/add"


def create_coupon(
    env_name,
    backend_email,
    backend_password,
    title,
    discount,
    date_s,
    date_e,
    prefix,
    piece="1",
    regions=None,
    service_items=None,
    coupon_type="不得與其他優惠券重複",
):
    """
    建立優惠券。

    參數：
        title        : 優惠券標題（通常為客人姓名或用途說明）
        discount     : 面額（整數，元）
        date_s       : 有效期限起（YYYY-MM-DD）
        date_e       : 有效期限迄（YYYY-MM-DD）
        prefix       : 優惠碼前綴（系統會自動在後面加英文字母，例如 tpe0707 → tpe0707K）
        piece        : 張數（預設 1）
        regions      : list，例如 ["台北"]；None 則預設台北
        service_items: list，例如 ["居家清潔"]；None 則預設居家清潔
        coupon_type  : 優惠券種類（預設「不得與其他優惠券重複」）

    回傳 dict：
        success      : bool
        message      : 說明
        coupon_code  : 猜測的優惠碼（prefix + 第一個大寫字母，實際碼需至後台確認）
    """
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    # 取得 CSRF token（GET /coupon/add 頁面）
    coupon_add_url = f"{base_url}{COUPON_ADD_URL_PATH}"
    get_resp = session.get(coupon_add_url, headers=HEADERS, allow_redirects=True)
    if get_resp.status_code != 200:
        raise Exception(f"無法開啟優惠券新增頁面：HTTP {get_resp.status_code}")

    token = ""
    token_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', get_resp.text)
    if token_match:
        token = token_match.group(1)
    if not token:
        # fallback：從 hidden input 抓
        token_match2 = re.search(r'name=\"_token\"\s+value=\"([^\"]+)\"', get_resp.text)
        if token_match2:
            token = token_match2.group(1)
    if not token:
        raise Exception("無法取得 CSRF token，請確認已登入後台")

    regions = regions or ["台北"]
    service_items = service_items or ["居家清潔"]

    data = {
        "coupon_type_id": COUPON_TYPE_MAP.get(coupon_type, "1"),
        "title": str(title).strip(),
        "date_s": str(date_s).strip(),
        "date_e": str(date_e).strip(),
        "prefix": str(prefix).strip(),
        "discount": str(int(float(discount))),
        "piece": str(int(piece)),
        "_token": token,
    }

    # list of tuples 保留重複 key（company_id[], service_item[]）
    fields = [
        ("coupon_type_id", COUPON_TYPE_MAP.get(coupon_type, "1")),
        ("title", str(title).strip()),
        ("date_s", str(date_s).strip()),
        ("date_e", str(date_e).strip()),
        ("prefix", str(prefix).strip()),
        ("discount", str(int(float(discount)))),
        ("piece", str(int(piece))),
        ("_token", token),
    ]
    for r in (regions or ["台北"]):
        fields.append(("company_id[]", COUPON_COMPANY_ID_MAP.get(r, "1")))
    for s in (service_items or ["居家清潔"]):
        fields.append(("service_item[]", COUPON_SERVICE_ITEM_MAP.get(s, "1")))

    coupon_files3 = [(k, (None, v)) for k, v in fields]
    post_headers3 = {k: v for k, v in HEADERS.items() if k.lower() != "content-type"}
    post_resp = session.post(
        coupon_add_url,
        files=coupon_files3,
        headers=post_headers3,
        allow_redirects=True,
    )

    if post_resp.status_code not in (200, 302):
        snippet = post_resp.text[:200].replace("\n", " ")
        raise Exception(f"優惠券建立失敗：HTTP {post_resp.status_code}｜{snippet}")
    if post_resp.url and "add" in post_resp.url:
        raise Exception("優惠券建立失敗：後台驗證未通過，請確認區域/服務項目欄位")
    # 建完後取剛建的那張優惠碼
    coupon_code = _get_newest_coupon_code(session, base_url, str(prefix).strip())

    return {
        "success": True,
        "coupon_prefix": prefix,
        "coupon_code": coupon_code,
        "discount": int(float(discount)),
        "piece": int(piece),
        "message": f"優惠券建立成功，優惠碼：{coupon_code}",
    }


def get_stored_value(env_name, backend_email, backend_password, phone, clean_type_id="1"):
    """
    透過 /ajax/get_member 查詢會員的儲值金餘額。
    回傳 (int餘額, member_dict)；查無或無儲值金回傳 (0, None)。
    """
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗")

    page = session.get(f"{base_url}/booking/stored_value_routine", headers=HEADERS, allow_redirects=True)
    token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
    csrf = token_m.group(1) if token_m else ""

    ajax = session.post(
        f"{base_url}/ajax/get_member",
        data={
            "phone": str(phone).strip(),
            "_token": csrf,
            "clean_type_id": str(clean_type_id),
        },
        headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
        allow_redirects=True,
    )
    try:
        data = ajax.json()
    except Exception:
        return 0, None

    if data.get("return_code") == "0000":
        sv = int(data.get("storedValue") or 0)
        member = data.get("member", {})
        return sv, member
    return 0, None


def _day_type_from_date(date_text):
    """依服務日期自動判斷平日/週末；週一到週五=平日，週六日=週末。"""
    try:
        d = datetime.strptime(str(date_text), "%Y-%m-%d").date()
    except Exception:
        return "平日"
    return "週末" if d.weekday() >= 5 else "平日"


def calc_stored_value_plan(sv, new_service_price=None, day_type="平日", total_person_hours=None, zero_total_stored_order=True):
    """
    計算儲值金補價差方案。

    v7.7 規則：
    - 平日單價 600，週末單價 700。
    - 儲值金折抵單的金額 = 單價 × 人時（若未提供人時，才用最小倍數 >= 儲值金餘額）。
    - zero_total_stored_order=True 時，優惠券A = 儲值金折抵單金額，讓該單總金額為 0。
    - 優惠券B = 儲值金餘額，用於客付補價差訂單。
    """
    import math
    unit_price = 700 if str(day_type or "").strip() == "週末" else 600
    try:
        ph = int(float(total_person_hours or 0))
    except Exception:
        ph = 0
    if ph > 0:
        n = ph
        dummy_price = unit_price * ph
    else:
        n = math.ceil(sv / unit_price) if sv > 0 else 1
        dummy_price = n * unit_price

    coupon_a = dummy_price if zero_total_stored_order else max(dummy_price - sv, 0)
    coupon_b = sv
    customer_pays = (new_service_price - sv) if new_service_price else None
    return {
        "unit_price": unit_price,
        "dummy_price": dummy_price,
        "coupon_a": coupon_a,
        "coupon_b": coupon_b,
        "customer_pays": customer_pays,
        "n": n,
        "total_person_hours": ph or n,
        "stored_order_total_after_coupon": 0 if zero_total_stored_order else sv,
        "zero_total_stored_order": bool(zero_total_stored_order),
    }


def _invoice_payload(invoice_mode, member_email="", mobile_carrier="", company_title="", company_no=""):
    """將畫面發票選項轉成 quick_create_order 需要的欄位。"""
    mode = str(invoice_mode or "會員載具").strip()
    if mode == "手機載具":
        if not str(mobile_carrier or "").strip().startswith("/"):
            raise Exception("手機載具需以 / 開頭")
        return {
            "invoice_type_override": "2",
            "carrier_type_id_override": "2",
            "carrier_info": str(mobile_carrier).strip(),
            "company_title": "",
            "company_no": "",
            "payment_type": "B2C",
        }
    if mode == "三聯式":
        if not str(company_title or "").strip() or not str(company_no or "").strip():
            raise Exception("三聯式發票需填寫抬頭與統編")
        return {
            "invoice_type_override": "3",
            "carrier_type_id_override": "1",
            "carrier_info": "",
            "company_title": str(company_title).strip(),
            "company_no": str(company_no).strip(),
            "payment_type": "B2B",
        }
    return {
        "invoice_type_override": "2",
        "carrier_type_id_override": "1",
        "carrier_info": str(member_email or "").strip(),
        "company_title": "",
        "company_no": "",
        "payment_type": "B2C",
    }


def _stored_value_makeup_context(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date,
    period_s, hour, person, address="", region="", coupon_prefix_base="", coupon_valid_days=60,
):
    """共用查詢：會員、地址、區域、儲值金、計算方案。"""
    day_type = _day_type_from_date(service_date)
    sv, _ = get_stored_value(env_name, backend_email, backend_password, phone, clean_type_id)
    if sv <= 0:
        raise Exception("查無儲值金或儲值金餘額為 0")

    try:
        total_ph = int(float(person)) * int(float(hour))
    except Exception:
        total_ph = 0
    plan = calc_stored_value_plan(sv, None, day_type=day_type, total_person_hours=total_ph, zero_total_stored_order=True)

    lookup = quick_lookup_member(env_name, backend_email, backend_password, phone, clean_type_id)
    member_payload = lookup.get("member_payload")
    if not member_payload:
        raise Exception(f"電話 {phone} 查無會員資料")
    member = member_payload.get("member", {}) or {}
    addr_list = member.get("memberAddressList", []) or []
    selected_address = address or (addr_list[0].get("address", "") if addr_list else "")
    if not selected_address:
        raise Exception("會員沒有可用服務地址，請先至後台補地址")
    selected_region = region or get_region_by_address(selected_address, ACCOUNTS) or "台北"

    today_str = date.today().strftime("%Y-%m-%d")
    date_e = (date.today() + timedelta(days=int(coupon_valid_days))).strftime("%Y-%m-%d")
    suffix = str(coupon_prefix_base or phone)[-4:]
    return {
        "balance": sv,
        "plan": plan,
        "lookup": lookup,
        "member": member,
        "address": selected_address,
        "region": selected_region,
        "day_type": day_type,
        "today_str": today_str,
        "date_e": date_e,
        "prefix_a": f"svA{suffix}",
        "prefix_b": f"svB{suffix}",
    }


def stored_value_makeup_create_stored_order(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
    hour, person, address="", region="", coupon_prefix_base="", coupon_valid_days=60,
):
    """第一段：建立儲值金折抵單，並把該單總金額用優惠券A折到 0，再換成檸檬人。"""
    ctx = _stored_value_makeup_context(
        env_name, backend_email, backend_password, phone, clean_type_id, service_date,
        period_s, hour, person, address, region, coupon_prefix_base, coupon_valid_days,
    )
    regions = [ctx["region"]] if ctx.get("region") else list(COUPON_COMPANY_ID_MAP.keys())
    services = ["居家清潔", "裝修細清"]
    coupon_a = create_coupon(
        env_name, backend_email, backend_password,
        title=f"儲值金折抵歸零-{phone}", discount=ctx["plan"]["coupon_a"],
        date_s=ctx["today_str"], date_e=ctx["date_e"], prefix=ctx["prefix_a"], piece="1",
        regions=regions, service_items=services,
    )
    code_a = coupon_a.get("coupon_code") or coupon_a.get("coupon_prefix") or ctx["prefix_a"]
    stored_order = quick_create_order(
        env_name=env_name,
        payway="儲值金",
        region=ctx["region"],
        lookup_result=ctx["lookup"],
        address=ctx["address"],
        clean_type_id=clean_type_id,
        date_s=service_date,
        period_s=period_s,
        hour=str(hour),
        person=str(person),
        discount_code=code_a,
    )
    lemon_result = assign_lemon_cleaners_to_order(
        session=stored_order["session"],
        base_url=_configure_environment(env_name),
        order_no_a=stored_order["order_no"],
        service_date=service_date,
        period_s=period_s,
        person_count=str(person),
    )
    note = (
        f"儲值金補價差第一段：儲值金折抵單 {stored_order['order_no']}，"
        f"{ctx['day_type']}單價 {ctx['plan']['unit_price']} × {ctx['plan']['total_person_hours']}人時 = {ctx['plan']['dummy_price']}，"
        f"優惠券A全額折抵 {ctx['plan']['coupon_a']} 元，該單總金額應為 0，檸檬人勿動。"
    )
    _update_order_note(stored_order["session"], _configure_environment(env_name), stored_order["order_no"], note)
    return {
        "stage": "stored_order",
        "balance": ctx["balance"],
        "plan": ctx["plan"],
        "day_type": ctx["day_type"],
        "coupon_a": coupon_a,
        "stored_order": stored_order,
        "lemon_result": lemon_result,
        "note": note,
        "address": ctx["address"],
        "region": ctx["region"],
        "phone": phone,
        "clean_type_id": clean_type_id,
        "service_date": service_date,
        "period_s": period_s,
        "hour": str(hour),
        "person": str(person),
        "coupon_prefix_base": coupon_prefix_base or phone,
        "coupon_valid_days": coupon_valid_days,
    }


def stored_value_makeup_create_paid_order(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
    hour, person, customer_payway="ATM", invoice_mode="會員載具", mobile_carrier="",
    company_title="", company_no="", address="", region="", coupon_prefix_base="",
    coupon_valid_days=60, stored_order_no="", balance_override=None,
):
    """第二段：建立客付補價差單，優惠券B折抵原儲值金餘額。"""
    ctx = _stored_value_makeup_context(
        env_name, backend_email, backend_password, phone, clean_type_id, service_date,
        period_s, hour, person, address, region, coupon_prefix_base, coupon_valid_days,
    )
    if balance_override not in (None, ""):
        try:
            ctx["balance"] = int(float(balance_override))
            ctx["plan"]["coupon_b"] = ctx["balance"]
        except Exception:
            pass

    regions = [ctx["region"]] if ctx.get("region") else list(COUPON_COMPANY_ID_MAP.keys())
    services = ["居家清潔", "裝修細清"]
    coupon_b = create_coupon(
        env_name, backend_email, backend_password,
        title=f"儲值金補價差客付-{phone}", discount=ctx["plan"]["coupon_b"],
        date_s=ctx["today_str"], date_e=ctx["date_e"], prefix=ctx["prefix_b"], piece="1",
        regions=regions, service_items=services,
    )
    code_b = coupon_b.get("coupon_code") or coupon_b.get("coupon_prefix") or ctx["prefix_b"]
    invoice = _invoice_payload(
        invoice_mode,
        member_email=ctx["member"].get("email") or "",
        mobile_carrier=mobile_carrier,
        company_title=company_title,
        company_no=company_no,
    )
    paid_order = quick_create_order(
        env_name=env_name,
        payway=customer_payway,
        region=ctx["region"],
        lookup_result=ctx["lookup"],
        address=ctx["address"],
        clean_type_id=clean_type_id,
        date_s=service_date,
        period_s=period_s,
        hour=str(hour),
        person=str(person),
        discount_code=code_b,
        **invoice,
    )
    pair = f"儲值折抵單 {stored_order_no} + 客付補價差單 {paid_order['order_no']}" if stored_order_no else f"客付補價差單 {paid_order['order_no']}"
    note = (
        f"儲值金補價差第二段：{pair}，"
        f"客付單使用優惠券B折抵原儲值金餘額 {ctx['balance']} 元。"
    )
    _update_order_note(paid_order["session"], _configure_environment(env_name), paid_order["order_no"], note)
    return {
        "stage": "paid_order",
        "balance": ctx["balance"],
        "plan": ctx["plan"],
        "day_type": ctx["day_type"],
        "coupon_b": coupon_b,
        "paid_order": paid_order,
        "note": note,
        "line_message": build_line_message(paid_order),
        "address": ctx["address"],
        "region": ctx["region"],
        "stored_order_no": stored_order_no,
    }


def stored_value_makeup_convert(
    env_name,
    backend_email,
    backend_password,
    phone,
    clean_type_id,
    service_date,
    period_s,
    hour,
    person,
    day_type="",
    customer_payway="ATM",
    invoice_mode="會員載具",
    mobile_carrier="",
    company_title="",
    company_no="",
    address="",
    region="",
    coupon_prefix_base="",
    coupon_valid_days=60,
):
    """相容舊按鈕：仍可一次跑完，但內部已拆成先儲值金單、再客付單。"""
    first = stored_value_makeup_create_stored_order(
        env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
        hour, person, address, region, coupon_prefix_base, coupon_valid_days,
    )
    second = stored_value_makeup_create_paid_order(
        env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
        hour, person, customer_payway, invoice_mode, mobile_carrier, company_title, company_no,
        first["address"], first["region"], coupon_prefix_base, coupon_valid_days,
        stored_order_no=first["stored_order"].get("order_no", ""),
        balance_override=first["balance"],
    )
    note = first.get("note", "") + "\n" + second.get("note", "")
    return {
        "balance": first["balance"],
        "plan": first["plan"],
        "day_type": first["day_type"],
        "coupon_a": first.get("coupon_a"),
        "coupon_b": second.get("coupon_b"),
        "stored_order": first.get("stored_order"),
        "paid_order": second.get("paid_order"),
        "lemon_result": first.get("lemon_result"),
        "note": note,
        "line_message": second.get("line_message"),
        "address": first["address"],
        "region": first["region"],
    }

def parse_new_customer_order_text(raw_text):
    """
    將客服貼上的新客制式文字拆解成欄位。
    """
    text = str(raw_text or "").strip()
    result = {
        "name": "",
        "phone": "",
        "email": "",
        "address": "",
        "ping": "",
        "payway": "",
        "invoice_type": "",
        "invoice_title": "",
        "tax_id": "",
        "carrier": "",
        "requirement": "",
        "note": "",
    }
    if not text:
        return result

    def clean_value(value):
        return str(value or "").strip().strip("：:").strip()

    normalized = text.replace("：", ":")
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]

    label_map = [
        ("name", ["訂購人姓名", "姓名", "客人姓名"]),
        ("phone", ["訂購人電話", "電話", "手機", "客人電話"]),
        ("email", ["訂購人Email", "訂購人email", "Email", "email", "信箱"]),
        ("address", ["服務地址", "地址"]),
        ("ping", ["室內坪數", "坪數"]),
        ("payway", ["付款方式"]),
        ("invoice_type", ["發票載具", "發票方式", "載具類型", "發票"]),
        ("invoice_title", ["發票抬頭", "公司抬頭", "抬頭", "買受人"]),
        ("tax_id", ["統一編號", "統編", "公司統編", "買受人統編"]),
        ("carrier", ["載具號碼", "載碼", "載具", "統編資訊"]),
        ("requirement", ["服務需求", "需求", "服務條件"]),
    ]

    consumed = set()

    for idx, line in enumerate(lines):
        compact_line = line.replace(" ", "")
        for key, labels in label_map:
            for label in labels:
                compact_label = label.replace(" ", "")
                if compact_line.startswith(compact_label + ":"):
                    result[key] = clean_value(line.split(":", 1)[1])
                    consumed.add(idx)
                    break
                if compact_line == compact_label and idx + 1 < len(lines):
                    result[key] = clean_value(lines[idx + 1])
                    consumed.add(idx)
                    consumed.add(idx + 1)
                    break
            if idx in consumed:
                break

    if not result["carrier"]:
        for idx, line in enumerate(lines):
            value = line.strip()
            if re.match(r"^/[A-Za-z0-9.+-]{6,}$", value):
                result["carrier"] = value
                consumed.add(idx)
                break

    if not result["email"]:
        m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
        if m:
            result["email"] = m.group(0)

    if not result["phone"]:
        m = re.search(r"(?:\+?886[-\s]?)?0?9[\d\-\s]{8,12}", text)
        if m:
            result["phone"] = normalize_phone(m.group(0))

    if result.get("phone"):
        result["phone"] = normalize_phone(result["phone"])

    requirement_patterns = [
        r"(平日|週末|假日|不限).*(\d+)\s*人\s*(\d+(?:\.\d+)?)\s*小時",
        r"(\d+)\s*人\s*(\d+(?:\.\d+)?)\s*小時",
    ]
    if not result["requirement"]:
        for idx, line in enumerate(lines):
            if idx in consumed:
                continue
            if any(re.search(pattern, line) for pattern in requirement_patterns):
                result["requirement"] = line.strip()
                consumed.add(idx)
                break

    if not result["tax_id"]:
        tax_matches = re.findall(r"(?<!\d)\d{8}(?!\d)", text)
        if tax_matches:
            result["tax_id"] = tax_matches[0]

    notes = [line for idx, line in enumerate(lines) if idx not in consumed]
    result["note"] = "\n".join(notes).strip()

    return result


def quick_create_new_customer_order(env_name, backend_email, backend_password, customer):
    """
    新客建單入口（目前只做前端驗證，尚未接上新客建立會員 API）。
    """
    required = ["name", "phone", "email", "address", "payway", "clean_type_id"]
    missing = [key for key in required if not str((customer or {}).get(key, "")).strip()]
    if missing:
        raise Exception("新客資料不足，請補齊：" + "、".join(missing))

    carrier = str((customer or {}).get("carrier", "")).strip()
    invoice_type = str((customer or {}).get("invoice_type", "")).strip()
    if invoice_type == "手機載具" and not carrier.startswith("/"):
        raise Exception("手機載具格式可能不正確，範例：/T8K346B")

    raise Exception(
        "新客資料已完成前端收集與驗證；但目前 quick_order.py 的既有建單核心需要會員 addressId，"
        "尚未接上新客建立會員/地址或新客訂單送出 API。請先依此資料至後台建立會員，"
        "或下一版新增 create_new_member_and_order() 後再直接送單。"
    )
