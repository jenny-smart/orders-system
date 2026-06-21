# ============================================================
# 檔名：quick_order_6.py
# 版本：v6
# 模組：單筆服務訂單後端模組
# 建立日期：2026-06-22
# 最後更新：2026-06-22
#
# Change Log
# v6
# - 依使用者指定下載檔名重新輸出版本檔
# - 保留服務訂單系統、功能選單與單筆建單功能調整
# - 檔名與 Header 版本一致，方便 GitHub / 本機備份辨識
# ============================================================
# -*- coding: utf-8 -*-
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

    歷史服務查詢只用 phone= 與 purchase_status=1，直接對齊後台
    「電話 + 付款狀態：已付款」的認定；不再把姓名、地址、服務類別、
    付款方式或發票條件混進來。

    同時記錄診斷資訊（實際請求網址、回應狀態、是否疑似被導回登入頁、
    抓到幾筆區塊），查無結果時可以直接看是真的沒資料還是請求本身有問題。
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

    重要修正：訂單區塊裡同時存在兩個日期字串：
    - 訂購日期：格式為 "YYYY-MM-DD HH:MM:SS"（帶秒數的時間戳記），
      在文字流裡通常排在「服務日期」前面（訂購資訊欄位先出現）。
    - 服務日期：格式為 "YYYY-MM-DD (週幾)"，例如 "2026-07-04 (六)"，
      後面緊接著服務時段 "HH:MM - HH:MM"（無秒數）。

    舊版邏輯「抓文字裡第一個日期」會誤抓到訂購日期。
    這裡改成優先找「日期 + 星期幾括號」這個服務日期專屬的格式特徵；
    真的找不到才退而求其次，找一個「後面不是緊接著 HH:MM:SS」的日期
    （排除訂購日期戳記）。

    回傳 (日期, "起 - 迄" 時段字串)，抓不到回傳 ("", "")。
    """
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*[（(][一二三四五六日][）)]", joined_text)

    if not date_match:
        for m in re.finditer(r"(\d{4}-\d{2}-\d{2})", joined_text):
            tail = joined_text[m.end():m.end() + 12]
            # 訂購日期格式緊接著 " HH:MM:SS"，排除掉；剩下的才當作候選服務日期
            if not re.match(r"\s*\d{1,2}:\d{2}:\d{2}", tail):
                date_match = m
                break

    if not date_match:
        return "", ""

    service_date = date_match.group(1)

    tail = joined_text[date_match.end():date_match.end() + 600]
    # 時段格式為 "HH:MM - HH:MM"（無秒數），用 (?!:) 避免誤吃到 HH:MM:SS 的前半段
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
    一般客訂單會明確顯示「付款方式：信用卡」或「付款方式：ATM」；
    儲值金訂單通常不會有這行，改用區塊內出現「儲值金」字樣判斷。
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
        # 付款方式不可用 fallback 猜，猜錯訊息樣板/金額會整個錯，
        # 寧可請客服去後台確認真實付款方式，不要冒險帶錯。
        raise Exception(f"訂單 {order_no} 無法判斷付款方式（信用卡/ATM/儲值金），請至後台『訂單管理』確認後手動處理，不要使用本功能猜測。")
    if payway != "儲值金" and not service_amount:
        raise Exception(f"訂單 {order_no} 缺少服務金額，無法產生通知")

    result = {
        "order_no": block["order_no"],
        "address": address,
        "date": service_date,
        "period": service_time,
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

    重要：判斷客人「是否曾被服務過」一律只看「電話 + 已付款」，
    不應該因為這次服務類別（例如裝修細清 vs 居家清潔）不同
    就誤判成新客人——客人是同一個人，只是買了不同服務。

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

    跟 get_last_paid_summary() 不同：那個是抓「全部地址中最新一筆」，
    這個是「每個地址各自的最新一筆」，用於多地址客人完整顯示各地點服務史，
    避免只看到全域最新那筆、其他地址的服務紀錄被蓋掉看不到。

    地址對不到（matched address 為空）的訂單不計入任何地址。
    超過 within_days 天數的不算「近一年內」，回傳該地址為 None。
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
            continue  # 已經有更新的這個地址訂單了（paid_orders 已由新到舊排序）
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
    地址/服務類別/付款方式/發票都直接從這筆抓出來，作為這次建單的預設值。

    若最近服務日期當天有多筆訂單（例如同天約了不同住處/不同服務），
    same_date_orders 會列出當天所有筆數，避免客服誤判成只有一筆。
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

    用途：避免客服明明是要幫客人「異動」原本的服務時間，
    卻誤建立第二筆新訂單，造成重複收費/重複派工。
    儲值金訂單沒有付款方式/發票概念，畫面顯示時請自行省略。
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
    payway,             # "信用卡" / "ATM" / "儲值金"
    region,              # 決定 ATM 收款帳戶 / 訊息區域
    lookup_result,       # quick_lookup_member() 回傳值
    address,             # 選定的地址字串（需與會員下拉地址其中一筆一致）
    clean_type_id,       # "1" 居家清潔／"2" 辦公室清潔／"3" 裝修細清
    date_s,              # "2026-06-25"
    period_s,            # 系統標準時段，例如 "09:00-12:00"
    hour,                 # 時數
    person="2",
    fallback_fare="0",
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

    # 真實車馬費：比照批次流程，從 check_contain 回傳的 purchase/area 多處欄位掃描，
    # 找不到才用呼叫端傳入的 fallback_fare（預設 0），不可隨意帶固定金額（例如舊版誤帶 200）。
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

    # 發票欄位：/booking/single（信用卡/ATM 走這條）後台表單把「發票類別」設為必填，
    # 之前完全沒帶這幾個欄位，後台驗證沒過、根本沒建立訂單，
    # 但畫面上看起來像「建單失敗」訊息，其實是表單沒送成功。
    # 優先沿用 check_contain 回傳的上次發票設定，沒有才用預設值（個人二聯式 + 會員載具/email）。
    invoice_type = str(
        first_nonzero(
            purchase_info.get("invoiceType") if purchase_info else "",
            find_nested_value(addr_check, ["invoiceType", "invoice_type"]),
            default="2",
        )
    )
    carrier_type_id = str(
        first_nonzero(
            purchase_info.get("carrierTypeId") if purchase_info else "",
            default="1",
        )
    )
    carrier_info = str(
        purchase_info.get("carrierInfo") if purchase_info and purchase_info.get("carrierInfo") else (member.get("email") or "")
    )
    company_title = str(purchase_info.get("companyTitle", "") if purchase_info else "")
    company_no = str(purchase_info.get("companyNo", "") if purchase_info else "")
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
        "discount_code": "",
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

    # 模擬手動「計算時數」：price/fare 留空讓後台計算（回傳值為未稅服務費）
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

    # 確認該時段有班表/人力
    slot = f"{date_s}_{period_s}"
    raw_section = get_section_raw(session, base_data, token, slot)
    if not slot_exists_in_section_response(raw_section, slot):
        raise Exception(f"該時段無班表：{slot}")

    cleaners = extract_cleaners_from_section_response(raw_section, slot)
    staff_display = format_staff_from_cleaners(cleaners, people=person)

    booking_url = _booking_url_for_payway(base_url, payway)

    # 送出建單前，先記錄此電話目前有哪些訂單編號，
    # 用來在送出後判斷「是否真的產生新訂單」，
    # 避免撞到同日期同時段的舊訂單時被誤判成功。
    before_order_nos = list_order_numbers_for_phone(session, phone, name=member_name)

    booking_resp = session.post(
        booking_url,
        data=_build_booking_submit_data(base_data, token, payway, slot),
        headers=HEADERS,
        allow_redirects=True,
    )
    time.sleep(1)

    after_order_nos = list_order_numbers_for_phone(session, phone, name=member_name)
    new_order_nos = after_order_nos - before_order_nos

    display_period = display_period_text(period_s.split("-")[0], period_s.split("-")[1])

    if not new_order_nos:
        # 把後台實際回應狀態/網址/內容片段一起附上，
        # 才看得出來是真的撞單，還是表單驗證沒過（例如缺必填欄位）導致根本沒送出。
        debug_snippet = booking_resp.text[:300].replace("\n", " ").strip()
        raise Exception(
            "建單失敗：系統未產生新訂單編號（可能該客人此時段已有訂單存在、後台拒絕重複預約，"
            "或表單缺少必填欄位導致後台驗證沒過）。請至後台『訂單管理』手動確認，不要直接使用畫面上顯示的舊訂單資訊。\n"
            f"［除錯資訊］回應狀態：{booking_resp.status_code}，回應網址：{booking_resp.url}\n"
            f"回應片段：{debug_snippet}"
        )

    if len(new_order_nos) == 1:
        order_no = next(iter(new_order_nos))
    else:
        # 理論上一次只會新增一筆，若意外抓到多筆，
        # 用日期/時段再比對一次，縮小範圍。
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
        "price": price_no_tax,              # 後台原始未稅服務費，保留供除錯核對
        "price_with_tax": price_with_tax,    # 含稅金額，訊息/畫面顯示用這個
        "service_amount": price_with_tax,     # 對外顯示的服務金額一律含稅
        "fare": base_data["fare"],
        "payway": payway,
        "region": region,
        "staff": meta.get("服務人員") or staff_display,
        "service_status": meta.get("服務狀態", "未處理"),
        "env_name": env_name,
        "session": session,
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
    """
    payway = order_result["payway"]
    region = order_result["region"]
    date_disp = order_result["date"].replace("-", "/")
    period = str(order_result["period"]).replace(" ", "")
    price = order_result.get("service_amount") or order_result.get("price_with_tax", order_result.get("price"))
    fare = order_result["fare"]
    address = order_result["address"]
    order_no = order_result["order_no"]
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
        return f"""感謝您於 檸檬家事 預約【居家清潔】服務！
服務時間 : {date_disp}  {period}
服務金額：{price}（含稅）
{card_fare_line}服務地址：{address}
※麻煩您於『明天 24:00前』完成付款，為保留他人訂購權利，逾期付款訂單將自動取消

{common_footer}

線上刷卡流程:
https://www.lemonclean.com.tw/order/{order_last6}
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
            # 台中帳戶；其餘區域（桃園/新竹/高雄）目前沿用台中帳戶，
            # 若日後各區開獨立帳戶，這裡再依 region 擴充對照表。
            bank_block = """銀行戶名：泳檬有限公司
銀行代碼 台北富邦銀行(012)-營業部
銀行帳號 00200102520512"""
            extra_note = ""

        atm_pay_title = "▲請您依下列匯款帳戶資訊繳費，謝謝！" if region == "台北" else "請您依下列匯款帳戶資訊繳費，謝謝！"
        extra_note_block = f"\n{extra_note}" if extra_note else ""
        service_lines = (
            f"服務時間 : {date_disp}  {period}\n{taipei_atm_fare_line}服務地址：{address}"
            if region == "台北"
            else f"服務時間 : {date_disp}  {period}\n服務地址：{address}{taichung_atm_fare_line}"
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
    例：2人6小時 = 12人時，會同時提供 2人6小時與 3人4小時。
    目前先提供最常用的原方案與可整除替代方案，避免列出太多不實用組合。
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

    # 常用顯示順序：原方案優先，其餘依人數由少到多。
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

    使用情境：客人未指定日期，只說「平日」、「週末」、「不限」、
    「上午」、「下午」、「不限時段」與人時需求時使用。

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
                # 若沒有剛好符合時數的標準時段，就跳過該方案，避免送錯班表時段。
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


def quick_create_new_customer_order(env_name, backend_email, backend_password, customer):
    """
    新客建單入口。

    目前舊 quick_create_order() 依賴後台會員 payload 與既有 addressId，
    因此新客若要直接建立訂單，需要另接「建立會員/地址」或「新客訂單」後台流程。
    這個函式先集中驗證新客資料，避免 UI 與後端邏輯混在一起。
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
