# ============================================================
# 檔名：quick_order.py
# 版本：v8.4
# 最後更新：2026-06-29
#
# Change Log
# v8.4
# - 新增 _assign_mixed_cleaners_to_order：配班優先用排班頁現有一般專員，不足再補檸檬人。
# - 新增 convert_order_multi：一張原單A → 多筆新單B1/B2/B3，每筆各建一張折價券。
#   原單A配班走混合邏輯。備註格式：A+B1+B2+B3 合併服務。
# v8.3 - 排班換人必須勾選足夠不同的檸檬人
# v8.2 - 檸檬人補勾依序檸檬人1/2/3
# v8.1 - 儲值金補價差第二段沿用第一段餘額
# v8.0 - 檸檬人清單解析新增 shift 頁掃描備援
# v7.9 - 檸檬人勾班衝突自動跳過
# v7.8 - 儲值金清零邏輯修正
# v7.7 - 儲值金補價差拆兩段
# v7.3 - PERIOD_DISPLAY_INFO / _format_period_display
# ============================================================
# -*- coding: utf-8 -*-
__version__ = "8.4"

import time
import re
from datetime import date, datetime, timedelta

import requests

import orders
from orders import (
    login, get_csrf_token, get_member, pick_best_address_info,
    geocode_address, check_contain, calculate_hour, extract_calc_fields,
    get_section_raw, slot_exists_in_section_response,
    extract_cleaners_from_section_response, format_staff_from_cleaners,
    fetch_order_meta_by_order_no, extract_order_cards_from_purchase_html,
    _extract_staff_line, send_confirmation_mail, normalize_phone,
    normalize_addr_for_match, display_period_text, first_nonzero,
    find_nested_value, get_region_by_address, HEADERS,
)
from accounts import ACCOUNTS
from env import BASE_URL_DEV, BASE_URL_PROD, ORDER_PREFIX_DEV, ORDER_PREFIX_PROD

PAYWAY_MAP = {"信用卡": "1", "ATM": "2", "儲值金": "4"}
BOOKING_ENDPOINT_MAP = {"信用卡": "/booking/single", "ATM": "/booking/single", "儲值金": "/booking/stored_value_routine"}
TAX_RATE = 1.05

PERIOD_DISPLAY_INFO = {
    "08:30-12:30": ("4小時", False), "09:00-11:00": ("2小時", False),
    "09:00-12:00": ("3小時", False), "14:00-16:00": ("2小時", False),
    "14:00-17:00": ("3小時", False), "14:00-18:00": ("4小時", False),
    "09:00-16:00": ("6小時", True), "09:00-18:00": ("8小時", True),
}

COUPON_COMPANY_ID_MAP = {"台北": "1", "桃園": "2", "新竹": "3", "台中": "4"}
COUPON_SERVICE_ITEM_MAP = {
    "居家清潔": "1", "辦公室清潔": "2", "裝修細清": "3", "年節大掃除": "4",
    "冷氣機清潔": "5", "洗衣機清潔": "6", "沙發/床墊清潔": "7", "整理收納": "8",
}
COUPON_TYPE_MAP = {
    "不得與其他優惠券重複": "1",
    "可重複使用，每個帳號限用一次": "2",
    "可重複使用，不限使用次數": "3",
}
COUPON_ADD_URL_PATH = "/coupon/add"

VALUE_TO_SHIFT_CODE = {
    "6": "全6", "8": "全8",
    "0830-1230": "上4", "0900-1200": "上3", "0900-1100": "上2",
    "1400-1600": "下2", "1400-1700": "下3", "1400-1800": "下4",
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
PERIOD_TO_SHIFT_CODE = {
    "09:00-12:00": "上3", "08:30-12:30": "上4", "09:00-11:00": "上2",
    "14:00-16:00": "下2", "14:00-17:00": "下3", "14:00-18:00": "下4",
    "09:00-16:00": "全6", "09:00-18:00": "全8",
}

PURCHASE_FILTER_PARAMS_TEMPLATE = {
    "keyword": "", "name": "", "phone": "", "orderNo": "",
    "date_s": "", "date_e": "", "clean_date_s": "", "clean_date_e": "",
    "paid_at_s": "", "paid_at_e": "", "refundDateS": "", "refundDateE": "",
    "buy": "", "area_id": "", "isCharge": "", "isRefund": "",
    "payway": "", "purchase_status": "", "progress_status": "",
    "invoiceStatus": "", "otherFee": "", "orderBy": "",
}
_LAST_PURCHASE_FETCH_DEBUG = {}
PURCHASE_STATUS_PAID = "1"


# =========================================================
# 基礎工具函式
# =========================================================

def _format_period_display(period_raw, person="", display_override=""):
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
    if person_str and person_str != "0":
        return f"{display}（{person_str}人）"
    return display


def _extract_actual_service_time(joined_text):
    m = re.search(r"簡訊實際服務時間\s*[：:]?\s*(\d{1,2}:\d{2})\s*[-~～]\s*(\d{1,2}:\d{2})", joined_text)
    if m:
        start, end = m.groups()
        return f"{start} - {end}"
    return ""


def _extract_phone_from_block_lines(lines):
    joined = "\n".join(lines)
    m = re.search(r"(?:\+?886[-\s]?)?0?9[\d\-\s]{8,10}", joined)
    if m:
        return normalize_phone(m.group(0))
    return ""


def _build_combined_period_display(orders_data):
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


def get_last_purchase_fetch_debug():
    return dict(_LAST_PURCHASE_FETCH_DEBUG)


def _block_matches_phone_filter(block, phone_norm):
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
    looks_like_login_page = "login" in resp.url.lower() or (len(raw_blocks) == 0 and "password" in resp.text.lower())
    effective_purchase_status = purchase_status
    fallback_info = {}
    if purchase_status and resp.status_code == 200 and not raw_blocks and not looks_like_login_page:
        fallback_params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
        fallback_params["phone"] = normalize_phone(phone)
        if name and not fallback_params["phone"]:
            fallback_params["name"] = name
        fallback_resp = session.get(orders.PURCHASE_URL, params=fallback_params, headers=HEADERS, allow_redirects=True)
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
        "request_url": getattr(resp.request, "url", ""), "final_url": resp.url,
        "status_code": resp.status_code, "purchase_status_filter": purchase_status,
        "effective_purchase_status_filter": effective_purchase_status,
        "raw_block_count": len(raw_blocks), "looks_like_login_page": looks_like_login_page,
        "snippet": resp.text[:300].replace("\n", " ").strip() if resp.status_code == 200 else "",
        **fallback_info,
    }
    if resp.status_code != 200:
        return []
    phone_norm = normalize_phone(phone)
    if not phone_norm:
        _LAST_PURCHASE_FETCH_DEBUG["filtered_block_count"] = len(raw_blocks)
        return raw_blocks
    filtered = [block for block in raw_blocks if _block_matches_phone_filter(block, phone_norm)]
    _LAST_PURCHASE_FETCH_DEBUG["filtered_block_count"] = len(filtered)
    return filtered


def list_order_numbers_for_phone(session, phone, name=""):
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
    staff_str = _extract_staff_line(lines)
    if not staff_str:
        return ""
    parts = [p.strip() for p in re.split(r"\s*X\s*", staff_str) if p.strip()]
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
        or "扣儲值金" in compact or "儲值金扣款" in compact
    ):
        return True
    if re.search(r"付款.{0,12}(完成|成功)", compact):
        return True
    return False


def _extract_invoice_line(joined_text):
    m = re.search(r"((?:二聯式|三聯式|捐贈發票)[：:][^\n]*)", joined_text)
    return m.group(1).strip() if m else ""


CLEAN_TYPE_LABELS = ["居家清潔", "辦公室清潔", "裝修細清", "大掃除"]


def _extract_clean_type_line(joined_text):
    for label in CLEAN_TYPE_LABELS:
        if label in joined_text:
            return label
    return ""


def _extract_label_value(lines, label, stop_labels):
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


def _period_to_shift_code(period_s):
    compact = str(period_s or "").replace(" ", "")
    return PERIOD_TO_SHIFT_CODE.get(compact, "")


def _shift_value_to_code(value):
    value = str(value or "").strip()
    return VALUE_TO_SHIFT_CODE.get(value, value)


def _shift_code_to_value(code):
    code = str(code or "").strip()
    for value, mapped in VALUE_TO_SHIFT_CODE.items():
        if mapped == code:
            return value
    return code


def _shift_code_to_group(code):
    code = str(code or "").strip()
    if code in {"全6", "全8"}:
        return "all"
    if code in {"上2", "上3", "上4"}:
        return "1"
    if code in {"下2", "下3", "下4"}:
        return "2"
    if code in {"晚2"}:
        return "3"
    return "1"


def _shift_codes_conflict(existing_code, target_code):
    existing_code = _shift_value_to_code(existing_code)
    target_code = _shift_value_to_code(target_code)
    if not existing_code or not target_code:
        return False
    if existing_code == target_code:
        return False
    if existing_code in {"全6", "全8"} or target_code in {"全6", "全8"}:
        return True
    return target_code in SHIFT_CONFLICT_TABLE.get(existing_code, set())


# =========================================================
# 會員查詢 & 訂單建立
# =========================================================

def quick_lookup_member(env_name, backend_email, backend_password, phone, clean_type_id="1"):
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")
    token = get_csrf_token(session)
    phone = normalize_phone(phone)
    member_payload = get_member(session, phone, token, clean_type_id)
    return {"session": session, "token": token, "phone": phone, "member_payload": member_payload, "base_url": base_url, "env_name": env_name}


def quick_check_available_slots(env_name, payway, lookup_result, address, clean_type_id, date_s, hour, person="2", periods=None, period_hours=None):
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
    addr_check = check_contain(session, member.get("member_id", ""), selected_address, best_addr.get("lat", ""), best_addr.get("lng", ""), token, clean_type_id)
    if not addr_check and lookup_result.get("token") and lookup_result.get("token") != token:
        addr_check = check_contain(session, member.get("member_id", ""), selected_address, best_addr.get("lat", ""), best_addr.get("lng", ""), lookup_result["token"], clean_type_id)
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
        "clean_type_id": clean_type_id, "phone": lookup_result.get("phone", ""),
        "name": str(member.get("name") or "").strip(), "email": str(member.get("email") or "").strip(),
        "tel": str(member.get("tel") or lookup_result.get("phone", "")),
        "line": str(member.get("line") or ""), "fbName": str(member.get("fb_name") or ""),
        "fb": str(member.get("fb") or ""), "memoProcess": str(member.get("memo_process") or ""),
        "memoFinance": str(member.get("memo_finance") or ""),
        "addressId": str(best_addr.get("addressId") or ""),
        "country_id": str(best_addr.get("country_id") or pick("country_id", "12")),
        "address": selected_address, "ping": str(pick("ping", "4")),
        "room": str(pick("room", "0")), "bathroom": str(pick("bathroom", "0")),
        "balcony": str(pick("balcony", "0")), "livingroom": str(pick("livingroom", "0")),
        "kitchen": str(pick("kitchen", "0")), "window": str(pick("window", "")),
        "shutter": str(pick("shutter", "")), "clothes": str(pick("clothes", "0")),
        "dyson": str(pick("dyson", "0")), "refrigerator": str(pick("refrigerator", "0")),
        "disinfection": str(pick("disinfection", "0")), "go_abord": str(pick("go_abord", "0")),
        "home_move": str(pick("home_move", "0")), "storage": str(pick("storage", "0")),
        "cabinet": str(pick("cabinet", "0")), "quintuple": str(pick("quintuple", "0")),
        "hour": str(int(float(hour))), "price": "", "price_vvip": "",
        "person": str(person), "date_s": date_s, "period_s": "", "period": "",
        "cycle": "1", "fare": "", "memo": "",
        "notice": str(best_addr.get("notice") or old_purchase.get("notice") or ""),
        "discount_code": "", "payway": PAYWAY_MAP.get(payway, "2"),
        "invoice_type": "2", "carrier_type_id": "1",
        "carrier_info": str(member.get("email") or ""),
        "company_title": "", "company_no": "", "donate_code": "8585", "is_backend": "477",
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
            rows.append({"date": date_s, "period": period, "available": False, "staff": "", "error": "計算時數失敗"})
            continue
        calc_fields = extract_calc_fields(calc_result, fallback_hours=data["hour"], fallback_fare="0")
        data["price"] = str(calc_fields.get("price") or "0")
        data["price_vvip"] = str(calc_fields.get("price_vvip") or "0")
        data["fare"] = str(calc_fields.get("fare") or "0")
        raw_section = get_section_raw(session, data, token, slot)
        available = slot_exists_in_section_response(raw_section, slot)
        cleaners = extract_cleaners_from_section_response(raw_section, slot) if available else []
        rows.append({"date": date_s, "period": period, "available": available, "staff": format_staff_from_cleaners(cleaners, people=person) if available else ""})
    return rows


def quick_create_order(
    env_name, payway, region, lookup_result, address, clean_type_id,
    date_s, period_s, hour, person="2", fallback_fare="0", discount_code="",
    payment_type="", carrier_info="", company_no="", company_title="",
    invoice_type_override="", carrier_type_id_override="",
):
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
        raise Exception(f"找不到對應地址資料：{address}")
    selected_address = str(best_addr.get("address") or address).strip()
    geo_lat, geo_lng = geocode_address(selected_address)
    if geo_lat and geo_lng:
        best_addr["lat"] = geo_lat
        best_addr["lng"] = geo_lng
    addr_check = check_contain(session, member.get("member_id", ""), selected_address, best_addr.get("lat", ""), best_addr.get("lng", ""), token, clean_type_id)
    if not addr_check and lookup_result.get("token") and lookup_result.get("token") != token:
        addr_check = check_contain(session, member.get("member_id", ""), selected_address, best_addr.get("lat", ""), best_addr.get("lng", ""), lookup_result["token"], clean_type_id)
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
    invoice_type = str(first_nonzero(invoice_type_override, purchase_info.get("invoiceType") if purchase_info else "", find_nested_value(addr_check, ["invoiceType", "invoice_type"]), default="2"))
    carrier_type_id = str(first_nonzero(carrier_type_id_override, purchase_info.get("carrierTypeId") if purchase_info else "", default="1"))
    carrier_info = str(first_nonzero(carrier_info, purchase_info.get("carrierInfo") if purchase_info else "", member.get("email") or "", default=""))
    company_title = str(first_nonzero(company_title, purchase_info.get("companyTitle", "") if purchase_info else "", default=""))
    company_no = str(first_nonzero(company_no, purchase_info.get("companyNo", "") if purchase_info else "", default=""))
    donate_code = str(purchase_info.get("donateCode", "8585") if purchase_info else "8585")
    old_purchase = best_addr.get("purchase", {}) if isinstance(best_addr.get("purchase"), dict) else {}

    def pick(key, default=""):
        value = old_purchase.get(key)
        return value if value not in (None, "") else default

    base_data = {
        "clean_type_id": clean_type_id, "phone": phone,
        "name": str(member.get("name") or "").strip(), "email": str(member.get("email") or "").strip(),
        "tel": str(member.get("tel") or phone), "line": str(member.get("line") or ""),
        "fbName": str(member.get("fb_name") or ""), "fb": str(member.get("fb") or ""),
        "memoProcess": str(member.get("memo_process") or ""), "memoFinance": str(member.get("memo_finance") or ""),
        "addressId": str(best_addr.get("addressId") or ""),
        "country_id": str(best_addr.get("country_id") or pick("country_id", "12")),
        "address": selected_address, "ping": str(pick("ping", "4")),
        "room": str(pick("room", "0")), "bathroom": str(pick("bathroom", "0")),
        "balcony": str(pick("balcony", "0")), "livingroom": str(pick("livingroom", "0")),
        "kitchen": str(pick("kitchen", "0")), "window": str(pick("window", "")),
        "shutter": str(pick("shutter", "")), "clothes": str(pick("clothes", "0")),
        "dyson": str(pick("dyson", "0")), "refrigerator": str(pick("refrigerator", "0")),
        "disinfection": str(pick("disinfection", "0")), "go_abord": str(pick("go_abord", "0")),
        "home_move": str(pick("home_move", "0")), "storage": str(pick("storage", "0")),
        "cabinet": str(pick("cabinet", "0")), "quintuple": str(pick("quintuple", "0")),
        "hour": str(int(float(hour))), "price": "", "price_vvip": "",
        "person": str(person), "date_s": date_s, "period_s": period_s,
        "period": "", "cycle": "1", "fare": "", "memo": "",
        "notice": str(best_addr.get("notice") or old_purchase.get("notice") or ""),
        "discount_code": str(discount_code or ""), "payment": str(payment_type or ""),
        "carrierInfo": str(carrier_info or ""), "companyNo": str(company_no or ""),
        "companyTitle": str(company_title or ""), "payway": PAYWAY_MAP.get(payway, "2"),
        "invoice_type": invoice_type, "carrier_type_id": carrier_type_id,
        "carrier_info": carrier_info, "company_title": company_title,
        "company_no": company_no, "donate_code": donate_code, "is_backend": "477",
        "member_id": str(member.get("member_id") or ""),
        "company_id": str(best_addr.get("company_id") or pick("company_id", "1")),
        "area_id": str(best_addr.get("area_id") or pick("area_id", "25")),
        "lat": str(best_addr.get("lat") or pick("lat", "")),
        "lng": str(best_addr.get("lng") or pick("lng", "")),
    }
    calc_result = calculate_hour(session, base_data, token)
    if not calc_result:
        raise Exception("計算時數失敗")
    calc_fields = extract_calc_fields(calc_result, fallback_hours=base_data["hour"], fallback_fare=best_addr.get("fare", "0"))
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
    booking_resp = session.post(booking_url, data=_build_booking_submit_data(base_data, token, payway, slot), headers=HEADERS, allow_redirects=True)
    display_period = display_period_text(period_s.split("-")[0], period_s.split("-")[1])
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
        for candidate in matched:
            if candidate not in before_order_nos:
                return candidate
        return matched[0] if matched else None

    if not new_order_nos:
        order_no = _find_matching_order_after_submit() if _booking_count_success(booking_resp) else None
        if not order_no:
            debug_snippet = booking_resp.text[:300].replace("\n", " ").strip()
            extra_hint = "後台回傳 count > 0，但訂單列表回查不到符合條件的新訂單；請檢查訂單管理是否已建立。" if _booking_count_success(booking_resp) else ""
            raise Exception(f"建單失敗：系統未產生新訂單編號。\n{extra_hint}\n回應狀態：{booking_resp.status_code}，網址：{booking_resp.url}\n片段：{debug_snippet}")
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
        "order_no": order_no, "address": selected_address, "date": date_s,
        "period": display_period, "period_s": period_s, "person": str(person),
        "price": price_no_tax, "price_with_tax": price_with_tax, "service_amount": price_with_tax,
        "fare": base_data["fare"], "payway": payway, "region": region,
        "staff": meta.get("服務人員") or staff_display, "service_status": meta.get("服務狀態", "未處理"),
        "env_name": env_name, "session": session,
    }


# =========================================================
# 優惠券 & 訂單備註工具
# =========================================================

def _get_newest_coupon_code(session, base_url, prefix):
    try:
        list_resp = session.get(f"{base_url}/coupon", headers=HEADERS, allow_redirects=True)
        if list_resp.status_code != 200:
            return prefix
        ids = re.findall(r"/coupon/detail/(\d+)", list_resp.text)
        if not ids:
            return prefix
        detail_resp = session.get(f"{base_url}/coupon/detail/{ids[0]}", headers=HEADERS)
        if detail_resp.status_code != 200:
            return prefix
        prefix_esc = re.escape(prefix)
        codes = re.findall(rf"\b{prefix_esc}[A-Za-z0-9]*\b", detail_resp.text)
        codes = [c for c in codes if len(c) > len(prefix)]
        return codes[0] if codes else prefix
    except Exception:
        return prefix


def _build_coupon_via_session(session, base_url, title, discount, date_s, date_e, prefix, piece, regions, service_items):
    """用既有 session 建優惠券，不重新登入。回傳實際優惠碼字串。"""
    coupon_add_url = f"{base_url}{COUPON_ADD_URL_PATH}"
    get_resp = session.get(coupon_add_url, headers=HEADERS, allow_redirects=True)
    if get_resp.status_code != 200:
        raise Exception("無法開啟優惠券新增頁面")
    token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', get_resp.text)
    csrf = token_m.group(1) if token_m else ""
    if not csrf:
        raise Exception("無法取得 CSRF token")
    coupon_fields = [
        ("coupon_type_id", "1"), ("title", str(title)),
        ("date_s", str(date_s)), ("date_e", str(date_e)),
        ("prefix", str(prefix)), ("discount", str(int(float(discount)))),
        ("piece", str(int(piece))), ("_token", csrf),
    ]
    for rn in (regions or ["台北", "台中"]):
        coupon_fields.append(("company_id[]", COUPON_COMPANY_ID_MAP.get(rn, "1")))
    for svc in (service_items or ["居家清潔", "裝修細清"]):
        coupon_fields.append(("service_item[]", COUPON_SERVICE_ITEM_MAP.get(svc, "1")))
    coupon_files = [(k, (None, v)) for k, v in coupon_fields]
    post_headers = {k: v for k, v in HEADERS.items() if k.lower() != "content-type"}
    session.post(coupon_add_url, files=coupon_files, headers=post_headers, allow_redirects=True)
    time.sleep(1)
    return _get_newest_coupon_code(session, base_url, str(prefix))


def create_coupon(
    env_name, backend_email, backend_password, title, discount,
    date_s, date_e, prefix, piece="1", regions=None, service_items=None,
    coupon_type="不得與其他優惠券重複",
):
    """獨立登入版本的優惠券建立，供 UI 直接呼叫。"""
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")
    regions = regions or ["台北"]
    service_items = service_items or ["居家清潔"]
    coupon_add_url = f"{base_url}{COUPON_ADD_URL_PATH}"
    get_resp = session.get(coupon_add_url, headers=HEADERS, allow_redirects=True)
    if get_resp.status_code != 200:
        raise Exception(f"無法開啟優惠券新增頁面：HTTP {get_resp.status_code}")
    token = ""
    token_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', get_resp.text)
    if token_match:
        token = token_match.group(1)
    if not token:
        token_match2 = re.search(r'name=\"_token\"\s+value=\"([^\"]+)\"', get_resp.text)
        if token_match2:
            token = token_match2.group(1)
    if not token:
        raise Exception("無法取得 CSRF token，請確認已登入後台")
    fields = [
        ("coupon_type_id", COUPON_TYPE_MAP.get(coupon_type, "1")),
        ("title", str(title).strip()), ("date_s", str(date_s).strip()),
        ("date_e", str(date_e).strip()), ("prefix", str(prefix).strip()),
        ("discount", str(int(float(discount)))), ("piece", str(int(piece))), ("_token", token),
    ]
    for r in (regions or ["台北"]):
        fields.append(("company_id[]", COUPON_COMPANY_ID_MAP.get(r, "1")))
    for s in (service_items or ["居家清潔"]):
        fields.append(("service_item[]", COUPON_SERVICE_ITEM_MAP.get(s, "1")))
    coupon_files3 = [(k, (None, v)) for k, v in fields]
    post_headers3 = {k: v for k, v in HEADERS.items() if k.lower() != "content-type"}
    post_resp = session.post(coupon_add_url, files=coupon_files3, headers=post_headers3, allow_redirects=True)
    if post_resp.status_code not in (200, 302):
        snippet = post_resp.text[:200].replace("\n", " ")
        raise Exception(f"優惠券建立失敗：HTTP {post_resp.status_code}｜{snippet}")
    if post_resp.url and "add" in post_resp.url:
        raise Exception("優惠券建立失敗：後台驗證未通過，請確認區域/服務項目欄位")
    coupon_code = _get_newest_coupon_code(session, base_url, str(prefix).strip())
    return {"success": True, "coupon_prefix": prefix, "coupon_code": coupon_code, "discount": int(float(discount)), "piece": int(piece), "message": f"優惠券建立成功，優惠碼：{coupon_code}"}


def _fetch_order_edit_id(session, order_no):
    params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
    params["orderNo"] = str(order_no).strip()
    resp = session.get(orders.PURCHASE_URL, params=params, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return None
    m = re.search(r"/purchase/edit/(\d+)", resp.text)
    return m.group(1) if m else None


def _update_order_note(session, base_url, order_no, note):
    try:
        edit_id = _fetch_order_edit_id(session, order_no)
        if not edit_id:
            return False, f"找不到訂單 {order_no} 的編輯 ID"
        edit_url = f"{base_url}/purchase/edit/{edit_id}"
        get_resp = session.get(edit_url, headers=HEADERS, allow_redirects=True)
        if get_resp.status_code != 200:
            return False, f"無法開啟編輯頁面：HTTP {get_resp.status_code}"
        token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', get_resp.text)
        csrf = token_m.group(1) if token_m else ""
        if not csrf:
            return False, "無法取得 CSRF token"
        existing = {}
        for m2 in re.finditer(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"[^>]*>', get_resp.text):
            existing[m2.group(1)] = m2.group(2)
        for m2 in re.finditer(r'<textarea[^>]+name="([^"]+)"[^>]*>([^<]*)</textarea>', get_resp.text):
            existing[m2.group(1)] = m2.group(2).strip()
        existing["_token"] = csrf
        existing["_method"] = "PUT"
        existing["memoProcess"] = note
        post_resp = session.post(edit_url, data=existing, headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"}, allow_redirects=True)
        success = post_resp.status_code in (200, 302)
        return success, f"HTTP {post_resp.status_code}"
    except Exception as e:
        return False, str(e)


# =========================================================
# 檸檬人勾班工具函式
# =========================================================

def _parse_cleaner_shift_page(html_text, date_str=None):
    token_m = re.search(r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']', html_text or "")
    csrf = token_m.group(1) if token_m else ""
    if not csrf:
        meta_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html_text or "")
        csrf = meta_m.group(1) if meta_m else ""
    checked_fields = []
    checked_codes_on_date = set()
    for m in re.finditer(r'<input\b[^>]*\bchecked\b[^>]*>', html_text or "", re.I):
        tag = m.group(0)
        name_m = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.I)
        value_m = re.search(r'\bvalue=["\']?([^"\'\s>]+)', tag, re.I)
        date_m = re.search(r'\bdate=["\']([^"\']+)["\']', tag, re.I)
        if not name_m or not value_m:
            continue
        name = name_m.group(1)
        value = value_m.group(1)
        checked_fields.append((name, value))
        d = date_m.group(1) if date_m else ""
        if date_str and d == date_str:
            checked_codes_on_date.add(_shift_value_to_code(value))
    return csrf, checked_fields, checked_codes_on_date


def _get_cleaner_shift_form_info(session, base_url, cleaner_id, date_str):
    ym = str(date_str)[:7]
    resp = session.get(f"{base_url}/cleaner1/{cleaner_id}/shift", params={"month": ym}, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return "", [], set(), f"HTTP {resp.status_code}"
    csrf, checked_fields, checked_codes = _parse_cleaner_shift_page(resp.text, date_str)
    return csrf, checked_fields, checked_codes, ""


def _get_cleaner_shifts_on_date(session, base_url, cleaner_id, date_str):
    _csrf, _fields, checked_codes, _msg = _get_cleaner_shift_form_info(session, base_url, cleaner_id, date_str)
    return checked_codes


def _search_lemon_cleaners(session, base_url, target_month=None, min_needed=0):
    entries = []
    seen_ids = set()
    seen_names = set()
    target_month = str(target_month or date.today().strftime("%Y-%m"))[:7]
    min_needed = int(min_needed or 0)

    def lemon_sort_key(item):
        m = re.search(r"檸檬人\s*(\d+)", item[1])
        return int(m.group(1)) if m else 9999

    def add_entry(cid, name):
        cid = str(cid or "").strip()
        name = re.sub(r"\s+", "", str(name or "").strip())
        m = re.search(r"檸檬人\d+", name)
        if m:
            name = m.group(0)
        if not cid or cid in seen_ids or "檸檬人" not in name:
            return
        if name in seen_names:
            return
        seen_ids.add(cid)
        seen_names.add(name)
        entries.append((cid, name))

    candidate_ids = []

    def add_candidate(cid):
        cid = str(cid or "").strip()
        if cid.isdigit() and cid not in candidate_ids:
            candidate_ids.append(cid)

    try:
        resp = session.get(f"{base_url}/cleaner1", params={"area_id": "", "keyword": "檸檬"}, headers=HEADERS, allow_redirects=True)
    except Exception:
        resp = None

    if resp is not None and resp.status_code == 200:
        html = resp.text or ""
        row_blocks = re.split(r"<tr\b", html, flags=re.I)
        for row in row_blocks:
            if "檸檬人" not in row:
                continue
            name_m = re.search(r"檸檬人\d+", row)
            ids = re.findall(r"/cleaner1/(\d+)(?=[/'\"?#])", row, re.I)
            ids += re.findall(r"cleaner[_-]?id[=:'\" ]+(\d+)", row, re.I)
            for cid in ids:
                add_candidate(cid)
                if name_m:
                    add_entry(cid, name_m.group(0))
        for m in re.finditer(r"/cleaner1/(\d+)(?=[/'\"?#])", html, re.I):
            cid = m.group(1)
            ctx = html[max(0, m.start() - 1000): m.end() + 1000]
            name_m = re.search(r"檸檬人\d+", ctx)
            add_candidate(cid)
            if name_m:
                add_entry(cid, name_m.group(0))

    entries.sort(key=lemon_sort_key)
    if min_needed and len(entries) >= min_needed:
        return entries

    for cid in list(range(1, 501)):
        add_candidate(cid)

    for cid in candidate_ids:
        if str(cid) in seen_ids:
            continue
        try:
            r = session.get(f"{base_url}/cleaner1/{cid}/shift", params={"month": target_month}, headers=HEADERS, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        txt = r.text or ""
        name_m = re.search(r"專員\s*[：:]\s*(?:<[^>]+>\s*)*(檸檬人\d+)", txt)
        if not name_m:
            name_m = re.search(r"<label>\s*(檸檬人\d+)\s*</label>", txt)
        if name_m:
            add_entry(cid, name_m.group(1))
            entries.sort(key=lemon_sort_key)
            if min_needed and len(entries) >= min_needed:
                break

    entries.sort(key=lemon_sort_key)
    return entries


def _set_cleaner_shift_if_available(session, base_url, cleaner_id, cleaner_name, date_str, target_shift_code):
    csrf, checked_fields, checked_codes, err = _get_cleaner_shift_form_info(session, base_url, cleaner_id, date_str)
    if err:
        return {"success": False, "name": cleaner_name, "id": cleaner_id, "reason": err, "checked": sorted(checked_codes)}
    target_shift_code = _shift_value_to_code(target_shift_code)
    conflicts = sorted(c for c in checked_codes if _shift_codes_conflict(c, target_shift_code))
    if conflicts:
        return {"success": False, "name": cleaner_name, "id": cleaner_id, "reason": f"{date_str} 已勾 {'、'.join(conflicts)}，與 {target_shift_code} 衝突", "checked": sorted(checked_codes)}
    if target_shift_code in checked_codes:
        return {"success": True, "name": cleaner_name, "id": cleaner_id, "message": f"{cleaner_name} {date_str} 已有 {target_shift_code} 勾班", "checked": sorted(checked_codes), "already_checked": True}
    target_name = f"shift_{date_str}_{_shift_code_to_group(target_shift_code)}"
    target_value = _shift_code_to_value(target_shift_code)
    fields = []
    if csrf:
        fields.append(("_token", csrf))
    seen = set()
    for name, value in checked_fields:
        key = (name, value)
        if key in seen:
            continue
        seen.add(key)
        fields.append((name, value))
    if (target_name, target_value) not in seen:
        fields.append((target_name, target_value))
    resp = session.post(f"{base_url}/cleaner1/{cleaner_id}/shift", params={"month": str(date_str)[:7]}, data=fields, headers=HEADERS, allow_redirects=True)
    ok = resp.status_code in (200, 302)
    return {
        "success": ok, "name": cleaner_name, "id": cleaner_id,
        "message": f"{cleaner_name} 已補勾 {date_str} {target_shift_code}" if ok else f"POST 失敗：HTTP {resp.status_code}",
        "checked": sorted(checked_codes), "target": target_shift_code,
    }


def ensure_lemon_cleaner_shifts(session, base_url, service_date, period_s, person_count):
    target_shift_code = _period_to_shift_code(period_s)
    if not target_shift_code:
        return {"success": False, "message": f"無法判斷服務時段 {period_s} 對應班別", "assigned": [], "skipped": []}
    cleaners = _search_lemon_cleaners(session, base_url, target_month=str(service_date)[:7], min_needed=int(person_count))
    if not cleaners:
        return {"success": False, "message": "找不到檸檬人清單", "assigned": [], "skipped": []}
    need = int(person_count)
    assigned = []
    assigned_ids = []
    skipped = []
    seen_candidate_names = set()
    seen_candidate_ids = set()
    for cleaner_id, cleaner_name in cleaners:
        if str(cleaner_id) in seen_candidate_ids or str(cleaner_name) in seen_candidate_names:
            continue
        seen_candidate_ids.add(str(cleaner_id))
        seen_candidate_names.add(str(cleaner_name))
        if len(assigned) >= need:
            break
        result = _set_cleaner_shift_if_available(session, base_url, cleaner_id, cleaner_name, service_date, target_shift_code)
        if result.get("success"):
            assigned.append(cleaner_name)
            assigned_ids.append(str(cleaner_id))
        else:
            skipped.append(result)
    ok = len(assigned) >= need
    return {
        "success": ok,
        "message": f"已預先補勾檸檬人：{'、'.join(assigned)}" if ok else f"可用檸檬人不足：需要 {need} 位，找到 {len(assigned)} 位",
        "assigned": assigned, "assigned_ids": assigned_ids, "skipped": skipped, "target_shift_code": target_shift_code,
    }


def _get_schedule_edit_info(session, base_url, date_str, purchase_id):
    resp = session.get(f"{base_url}/schedule/edit", params={"date": date_str, "purchase_id": purchase_id}, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return None, [], []
    token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', resp.text)
    csrf = token_m.group(1) if token_m else ""
    origin_ids = re.findall(r'name=["\']originShiftId\[\]["\'][^>]*value=["\']?(\d+)["\']?', resp.text)
    if not origin_ids:
        origin_ids = re.findall(r'value=["\']?(\d+)["\'][^>]*name=["\']originShiftId\[\]', resp.text)
    slots = []
    slot_blocks = re.split(r'name=["\']originShiftId\[\]', resp.text)[1:]
    for block in slot_blocks:
        slot_map = {}
        for m in re.finditer(r'<label[^>]+for=["\']shift_\d+_(\d+)["\'][^>]*>([^<]+)</label>', block):
            shift_id = m.group(1)
            name = m.group(2).strip()
            slot_map[name] = shift_id
        slots.append(slot_map)
    return csrf, origin_ids, slots


def assign_lemon_cleaners_to_order(session, base_url, order_no_a, service_date, period_s, person_count):
    """全換檸檬人（v8.3 邏輯）。用於需要所有位置都換成檸檬人的情境。"""
    purchase_id = _fetch_order_edit_id(session, order_no_a)
    if not purchase_id:
        return {"success": False, "message": f"無法取得訂單 {order_no_a} 的後台 ID"}
    pre_shift_result = ensure_lemon_cleaner_shifts(session=session, base_url=base_url, service_date=service_date, period_s=period_s, person_count=person_count)
    if not pre_shift_result.get("success"):
        return {"success": False, "message": pre_shift_result.get("message", "檸檬人勾班失敗"), "pre_shift_result": pre_shift_result}
    preferred_names = list(pre_shift_result.get("assigned") or [])
    csrf, origin_ids, slots = _get_schedule_edit_info(session, base_url, service_date, purchase_id)
    if not slots:
        return {"success": False, "message": "無法取得排班修改頁，請手動操作"}
    n = int(person_count)
    shift_choices = []
    assigned_names = []
    missing_slots = []

    def _lemon_name_no(name):
        m = re.search(r"檸檬人\s*(\d+)", str(name or ""))
        return int(m.group(1)) if m else 9999

    used_names = set()
    for i, slot_map in enumerate(slots):
        if i >= n:
            break
        lemon_entry = None
        for preferred in preferred_names:
            if preferred in used_names:
                continue
            for name_key, sid in slot_map.items():
                clean_name = name_key.strip()
                if "檸檬人" not in clean_name or clean_name in used_names:
                    continue
                if preferred == clean_name or preferred in clean_name or clean_name in preferred:
                    lemon_entry = (clean_name, sid)
                    break
            if lemon_entry:
                break
        if not lemon_entry:
            lemon_candidates = [(name_key.strip(), sid) for name_key, sid in slot_map.items() if "檸檬人" in name_key and name_key.strip() not in used_names]
            lemon_candidates.sort(key=lambda x: _lemon_name_no(x[0]))
            if lemon_candidates:
                lemon_entry = lemon_candidates[0]
        if lemon_entry:
            shift_choices.append(lemon_entry[1])
            assigned_names.append(lemon_entry[0])
            used_names.add(lemon_entry[0])
        else:
            missing_slots.append(i + 1)
    if missing_slots:
        available_by_slot = [{"slot": idx, "lemon_candidates": [name for name in slot_map.keys() if "檸檬人" in name]} for idx, slot_map in enumerate(slots[:n], start=1)]
        return {"success": False, "message": f"槽位 {missing_slots} 找不到可用的檸檬人；訂單需要 {n} 位不同檸檬人，已選 {len(assigned_names)} 位。", "available_by_slot": available_by_slot, "pre_shift_result": pre_shift_result}
    fields = [("_token", csrf), ("_method", "PUT")]
    for oid in origin_ids:
        fields.append(("originShiftId[]", oid))
    for j, sid in enumerate(shift_choices):
        fields.append((f"shiftId[{j}]", sid))
    post_resp = session.post(f"{base_url}/schedule/edit", params={"date": service_date, "purchase_id": purchase_id}, data=fields, headers=HEADERS, allow_redirects=True)
    success = post_resp.status_code in (200, 302)
    return {
        "success": success, "assigned": assigned_names, "pre_shift_result": pre_shift_result,
        "message": f"已將配班改為：{'、'.join(assigned_names)}" if success else f"POST 失敗：HTTP {post_resp.status_code}",
    }


def _assign_mixed_cleaners_to_order(session, base_url, order_no, service_date, period_s, person_count):
    """
    v8.4 混合配班：優先用排班頁現有一般專員，不足再補檸檬人。
    回傳 dict: success / assigned / assigned_types / message
    """
    purchase_id = _fetch_order_edit_id(session, order_no)
    if not purchase_id:
        return {"success": False, "message": f"無法取得訂單 {order_no} 的後台 ID"}
    n = int(person_count)
    pre_shift_result = ensure_lemon_cleaner_shifts(session=session, base_url=base_url, service_date=service_date, period_s=period_s, person_count=person_count)
    preferred_lemon_names = list(pre_shift_result.get("assigned") or [])
    csrf, origin_ids, slots = _get_schedule_edit_info(session, base_url, service_date, purchase_id)
    if not slots:
        return {"success": False, "message": "無法取得排班修改頁，請手動操作"}
    shift_choices = []
    assigned_names = []
    assigned_types = []
    used_names = set()

    def _lemon_no(name):
        m = re.search(r"檸檬人\s*(\d+)", str(name or ""))
        return int(m.group(1)) if m else 9999

    for i, slot_map in enumerate(slots):
        if i >= n:
            break
        chosen = None
        chosen_type = None
        # 優先：一般專員
        normal_candidates = [(name_key.strip(), sid) for name_key, sid in slot_map.items() if "檸檬人" not in name_key and name_key.strip() not in used_names]
        if normal_candidates:
            chosen = normal_candidates[0]
            chosen_type = "一般"
        # 備用：檸檬人
        if not chosen:
            lemon_candidates = [(name_key.strip(), sid) for name_key, sid in slot_map.items() if "檸檬人" in name_key and name_key.strip() not in used_names]
            preferred = [c for c in lemon_candidates if c[0] in preferred_lemon_names]
            others = [c for c in lemon_candidates if c[0] not in preferred_lemon_names]
            preferred.sort(key=lambda x: _lemon_no(x[0]))
            others.sort(key=lambda x: _lemon_no(x[0]))
            all_lemon = preferred + others
            if all_lemon:
                chosen = all_lemon[0]
                chosen_type = "檸檬人"
        if chosen:
            shift_choices.append(chosen[1])
            assigned_names.append(chosen[0])
            assigned_types.append(chosen_type)
            used_names.add(chosen[0])
        else:
            return {"success": False, "message": f"槽位 {i+1} 找不到可用人員（一般專員或檸檬人），請手動操作", "assigned": assigned_names, "assigned_types": assigned_types}
    fields = [("_token", csrf), ("_method", "PUT")]
    for oid in origin_ids:
        fields.append(("originShiftId[]", oid))
    for j, sid in enumerate(shift_choices):
        fields.append((f"shiftId[{j}]", sid))
    post_resp = session.post(f"{base_url}/schedule/edit", params={"date": service_date, "purchase_id": purchase_id}, data=fields, headers=HEADERS, allow_redirects=True)
    success = post_resp.status_code in (200, 302)
    normal_count = assigned_types.count("一般")
    lemon_count = assigned_types.count("檸檬人")
    detail = []
    if normal_count:
        detail.append(f"一般專員 {normal_count} 位")
    if lemon_count:
        detail.append(f"檸檬人 {lemon_count} 位")
    return {
        "success": success, "assigned": assigned_names, "assigned_types": assigned_types,
        "pre_shift_result": pre_shift_result,
        "message": f"配班已設為：{'、'.join(assigned_names)}（{'＋'.join(detail)}）" if success else f"POST 失敗：HTTP {post_resp.status_code}",
    }


# =========================================================
# 訂單轉換（一對一，原有邏輯）& 一對多（v8.4 新增）
# =========================================================

def convert_order(
    env_name, backend_email, backend_password, order_no_a,
    new_person, new_hour, new_date_s, new_period_s, clean_type_id="1",
):
    """一對一訂單轉換：原單A全換檸檬人，建折價券，建新單B。"""
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")
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
    today_str = date.today().strftime("%Y-%m-%d")
    coupon_prefix = order_no_a[-4:]
    coupon_discount = int(float(str(service_amount_a).replace(",", "")))
    coupon_code = _build_coupon_via_session(
        session, base_url, title=f"訂單轉換-{order_no_a}",
        discount=coupon_discount, date_s=today_str, date_e=service_date_a,
        prefix=coupon_prefix, piece=2, regions=["台北", "台中"], service_items=["居家清潔", "裝修細清"],
    )
    if not coupon_code or coupon_code == coupon_prefix:
        raise Exception(f"折價券建立失敗，請至後台確認")
    token_booking = get_csrf_token(session)
    member_payload = get_member(session, phone_a, token_booking, clean_type_id)
    if not member_payload:
        raise Exception(f"電話 {phone_a} 查無會員資料")
    lookup_result = {"session": session, "token": token_booking, "phone": phone_a, "member_payload": member_payload, "base_url": base_url, "env_name": env_name}
    order_b_result = quick_create_order(
        env_name=env_name, payway=payway_a, region=region_a, lookup_result=lookup_result,
        address=address_a, clean_type_id=clean_type_id, date_s=new_date_s,
        period_s=new_period_s, hour=new_hour, person=new_person, discount_code=coupon_code,
    )
    order_no_b = order_b_result["order_no"]
    combo_desc = f"{new_person}人{new_hour}小時"
    note_b = f"{order_no_a}+{order_no_b} 合併{combo_desc}服務"
    note_a = f"{order_no_a}+{order_no_b} 合併{combo_desc}服務，檸檬人勿動"
    note_a_ok, note_a_msg = _update_order_note(session, base_url, order_no_a, note_a)
    note_b_ok, note_b_msg = _update_order_note(session, base_url, order_no_b, note_b)
    line_msg = build_line_message(order_b_result)
    lemon_result = assign_lemon_cleaners_to_order(
        session=session, base_url=base_url, order_no_a=order_no_a,
        service_date=service_date_a, period_s=new_period_s, person_count=new_person,
    )
    return {
        "order_no_a": order_no_a, "order_no_b": order_no_b, "coupon_code": coupon_code,
        "lemon_result": lemon_result, "coupon_discount": coupon_discount,
        "service_date_a": service_date_a, "combo_desc": combo_desc,
        "note_a": note_a, "note_b": note_b,
        "note_a_ok": note_a_ok, "note_a_msg": note_a_msg,
        "note_b_ok": note_b_ok, "note_b_msg": note_b_msg,
        "edit_url_a": f"{base_url}/purchase/edit/{order_no_a.replace('LC00', '')}",
        "purchase_url_a": f"{base_url}/purchase?orderNo={order_no_a}",
        "line_message": line_msg, "order_b_result": order_b_result, "region": region_a,
    }


def convert_order_multi(
    env_name, backend_email, backend_password, order_no_a, new_orders, clean_type_id="1",
):
    """
    v8.4 一對多訂單轉換：原單A → 多筆新單B1/B2/B3...

    new_orders: list of dict，每筆包含：
        date_s   : 服務日期（YYYY-MM-DD）
        period_s : 服務時段
        hour     : 時數（整數）
        person   : 人數（整數）

    流程：
    1. 查原訂單A，混合配班（一般專員優先，不足補檸檬人）
    2. 逐筆新訂單：calculate_hour → 建折價券 → 建單 → 混合配班
    3. 備註自動寫入：A+B1+B2+B3 合併服務
    """
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    # ── Step 1: 查原訂單A ─────────────────────────────────────────
    block_a = _fetch_purchase_block_for_order_no(session, order_no_a)
    lines_a = block_a.get("lines", [])
    joined_a = "\n".join(lines_a)
    service_date_a, period_a_raw = _parse_service_date_time_loose(joined_a)
    address_a = _extract_address_line(lines_a)
    payway_a = _extract_payway_line(joined_a)
    phone_a = _extract_phone_from_block_lines(lines_a)
    region_a = get_region_by_address(address_a, ACCOUNTS) or "台北"
    _person_str, _ = _extract_person_hour_line(joined_a)
    person_a = int(_person_str) if _person_str and _person_str.isdigit() else len(new_orders)

    if not phone_a:
        raise Exception(f"無法從訂單 {order_no_a} 取得客人電話")
    if not address_a:
        raise Exception(f"訂單 {order_no_a} 無法取得服務地址")
    if not service_date_a:
        raise Exception(f"訂單 {order_no_a} 無法取得服務日期")

    # ── Step 2: 查會員 ────────────────────────────────────────────
    token_booking = get_csrf_token(session)
    member_payload = get_member(session, phone_a, token_booking, clean_type_id)
    if not member_payload:
        raise Exception(f"電話 {phone_a} 查無會員資料")
    lookup_result = {
        "session": session, "token": token_booking, "phone": phone_a,
        "member_payload": member_payload, "base_url": base_url, "env_name": env_name,
    }

    # ── Step 3: 原訂單A 混合配班 ──────────────────────────────────
    period_a_for_assign = new_orders[0].get("period_s", "") if new_orders else (period_a_raw.replace(" ", "") if period_a_raw else "")
    lemon_result_a = _assign_mixed_cleaners_to_order(
        session=session, base_url=base_url, order_no=order_no_a,
        service_date=service_date_a, period_s=period_a_for_assign, person_count=str(person_a),
    )

    # ── Step 4: 預先做一次 check_contain 取 area_id/company_id ───
    member = member_payload.get("member", {})
    best_addr = pick_best_address_info(member_payload, address_a)
    if not best_addr:
        raise Exception(f"找不到地址資料：{address_a}")
    selected_address = str(best_addr.get("address") or address_a).strip()
    geo_lat, geo_lng = geocode_address(selected_address)
    if geo_lat and geo_lng:
        best_addr["lat"] = geo_lat
        best_addr["lng"] = geo_lng
    token_for_calc = _get_booking_token_for_payway(session, base_url, payway_a)
    addr_check = check_contain(
        session, member.get("member_id", ""), selected_address,
        best_addr.get("lat", ""), best_addr.get("lng", ""), token_for_calc, clean_type_id,
    )
    if addr_check:
        area_info = addr_check.get("area") if isinstance(addr_check.get("area"), dict) else {}
        if area_info:
            best_addr["area_id"] = area_info.get("area_id", best_addr.get("area_id"))
            best_addr["company_id"] = area_info.get("company_id", best_addr.get("company_id"))
    old_purchase = best_addr.get("purchase", {}) if isinstance(best_addr.get("purchase"), dict) else {}

    def pick(key, default=""):
        v = old_purchase.get(key)
        return v if v not in (None, "") else default

    base_calc_data = {
        "clean_type_id": clean_type_id, "phone": phone_a,
        "name": str(member.get("name") or "").strip(),
        "email": str(member.get("email") or "").strip(),
        "tel": str(member.get("tel") or phone_a),
        "addressId": str(best_addr.get("addressId") or ""),
        "country_id": str(best_addr.get("country_id") or pick("country_id", "12")),
        "address": selected_address, "ping": str(pick("ping", "4")),
        "room": str(pick("room", "0")), "bathroom": str(pick("bathroom", "0")),
        "balcony": str(pick("balcony", "0")), "livingroom": str(pick("livingroom", "0")),
        "kitchen": str(pick("kitchen", "0")), "window": str(pick("window", "")),
        "shutter": str(pick("shutter", "")), "clothes": str(pick("clothes", "0")),
        "dyson": str(pick("dyson", "0")), "refrigerator": str(pick("refrigerator", "0")),
        "disinfection": str(pick("disinfection", "0")), "go_abord": str(pick("go_abord", "0")),
        "home_move": str(pick("home_move", "0")), "storage": str(pick("storage", "0")),
        "cabinet": str(pick("cabinet", "0")), "quintuple": str(pick("quintuple", "0")),
        "price": "", "price_vvip": "", "period": "", "cycle": "1",
        "fare": "", "memo": "", "notice": "",
        "payway": PAYWAY_MAP.get(payway_a, "2"),
        "invoice_type": "2", "carrier_type_id": "1",
        "carrier_info": str(member.get("email") or ""),
        "company_title": "", "company_no": "", "donate_code": "8585", "is_backend": "477",
        "member_id": str(member.get("member_id") or ""),
        "company_id": str(best_addr.get("company_id") or pick("company_id", "1")),
        "area_id": str(best_addr.get("area_id") or pick("area_id", "25")),
        "lat": str(best_addr.get("lat") or pick("lat", "")),
        "lng": str(best_addr.get("lng") or pick("lng", "")),
    }

    today_str = date.today().strftime("%Y-%m-%d")
    new_order_results = []
    new_order_nos = []

    for idx, new_order in enumerate(new_orders):
        new_date_s = new_order["date_s"]
        new_period_s = new_order["period_s"]
        new_hour = str(new_order["hour"])
        new_person = str(new_order["person"])

        try:
            # 4a. calculate_hour 取含稅金額
            calc_data = dict(base_calc_data)
            calc_data["hour"] = new_hour
            calc_data["person"] = new_person
            calc_data["date_s"] = new_date_s
            calc_data["period_s"] = new_period_s
            calc_data["discount_code"] = ""
            calc_result = calculate_hour(session, calc_data, token_for_calc)
            if not calc_result:
                raise Exception("calculate_hour 失敗")
            calc_fields = extract_calc_fields(calc_result, fallback_hours=new_hour, fallback_fare="0")
            price_no_tax = str(calc_fields.get("price") or "0")
            try:
                price_with_tax = int(round(float(price_no_tax) * TAX_RATE))
            except Exception:
                price_with_tax = 0
            if price_with_tax <= 0 and payway_a != "儲值金":
                raise Exception(f"金額計算為 0（{new_person}人{new_hour}小時），請確認設定")

            # 4b. 建折價券（面額=含稅金額，prefix=c{A後3碼}{序號}）
            coupon_prefix = f"c{order_no_a[-3:]}{idx+1}"
            coupon_code = _build_coupon_via_session(
                session, base_url,
                title=f"訂單轉換-{order_no_a}-B{idx+1}",
                discount=price_with_tax,
                date_s=today_str, date_e=new_date_s,
                prefix=coupon_prefix, piece=2,
                regions=["台北", "台中"], service_items=["居家清潔", "裝修細清"],
            )

            # 4c. 建新訂單
            order_result = quick_create_order(
                env_name=env_name, payway=payway_a, region=region_a,
                lookup_result=lookup_result, address=address_a,
                clean_type_id=clean_type_id, date_s=new_date_s, period_s=new_period_s,
                hour=new_hour, person=new_person, discount_code=coupon_code,
            )
            new_order_nos.append(order_result["order_no"])

            # 4d. 混合配班
            assign_result = _assign_mixed_cleaners_to_order(
                session=session, base_url=base_url, order_no=order_result["order_no"],
                service_date=new_date_s, period_s=new_period_s, person_count=new_person,
            )

            new_order_results.append({
                "index": idx + 1, "order_no": order_result["order_no"],
                "date_s": new_date_s, "period_s": new_period_s,
                "hour": new_hour, "person": new_person,
                "price_with_tax": price_with_tax, "coupon_code": coupon_code,
                "coupon_prefix": coupon_prefix, "assign_result": assign_result,
                "order_result": order_result,
                "line_message": build_line_message(order_result),
                "error": None,
            })

        except Exception as e:
            new_order_results.append({
                "index": idx + 1, "date_s": new_date_s, "period_s": new_period_s,
                "hour": new_hour, "person": new_person, "order_no": None, "error": str(e),
            })

    # ── Step 5: 備註文字並自動寫入 ──────────────────────────────
    b_nos = [r["order_no"] for r in new_order_results if r.get("order_no")]
    all_nos_str = "+".join([order_no_a] + b_nos)
    combo_desc = "、".join([f"{r['person']}人{r['hour']}小時" for r in new_order_results if r.get("order_no")])
    note_text = f"{all_nos_str} 合併服務（{combo_desc}）"
    note_a = f"{note_text}，原單配班請勿改動"
    note_a_ok, note_a_msg = _update_order_note(session, base_url, order_no_a, note_a)
    for r in new_order_results:
        if r.get("order_no"):
            _update_order_note(session, base_url, r["order_no"], note_text)

    return {
        "order_no_a": order_no_a,
        "new_order_results": new_order_results,
        "lemon_result_a": lemon_result_a,
        "note": note_text, "note_a": note_a,
        "note_a_ok": note_a_ok, "note_a_msg": note_a_msg,
        "purchase_url_a": f"{base_url}/purchase?orderNo={order_no_a}",
        "all_nos_str": all_nos_str,
        "success_count": len([r for r in new_order_results if r.get("order_no")]),
        "fail_count": len([r for r in new_order_results if not r.get("order_no")]),
    }


# =========================================================
# LINE 訊息 & 確認信
# =========================================================

def send_confirmation(order_result):
    session = order_result["session"]
    order_no = order_result["order_no"]
    return send_confirmation_mail(session, order_no)


def build_line_message(order_result):
    payway = order_result["payway"]
    region = order_result["region"]
    date_disp = order_result["date"].replace("-", "/")
    combined_period = str(order_result.get("combined_period", "") or "")
    if combined_period:
        period = combined_period
    else:
        period_raw = str(order_result.get("period_s") or order_result.get("period", "")).replace(" ", "")
        actual_period = str(order_result.get("actual_period", "") or "")
        person_cnt = str(order_result.get("person", "") or "")
        period = _format_period_display(period_raw, person_cnt, display_override=actual_period)
    price = order_result.get("service_amount") or order_result.get("price_with_tax", order_result.get("price"))
    fare = order_result["fare"]
    address = order_result["address"]
    order_no = order_result["order_no"]
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
    taipei_atm_fare_line = f"車馬費：{fare}（請現場支付給專員）\n" if has_fare else ""
    taichung_atm_fare_line = f"\n車馬費:{fare}（請現場支付給專員）" if has_fare else ""
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
        all_order_nos = order_result.get("all_order_nos") or [order_no]
        if len(all_order_nos) > 1:
            link_lines = []
            for i, ono in enumerate(all_order_nos, start=1):
                last6 = ono[-6:] if len(ono) >= 6 else ono
                link_lines.append(f"訂單{i}：https://www.lemonclean.com.tw/order/{last6}")
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


def build_line_message_from_order_no(env_name, backend_email, backend_password, order_no, fallback_region="台北"):
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")
    block = _fetch_purchase_block_for_order_no(session, order_no)
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
    region = get_region_by_address(address, ACCOUNTS) or fallback_region
    service_amount = _service_amount_from_block(joined, fare)
    if not service_date or not service_time:
        raise Exception(f"訂單 {order_no} 缺少服務日期或時段，無法產生通知")
    if not address:
        raise Exception(f"訂單 {order_no} 缺少服務地址，無法產生通知")
    if not payway:
        raise Exception(f"訂單 {order_no} 無法判斷付款方式（信用卡/ATM/儲值金），請至後台確認。")
    if payway != "儲值金" and not service_amount:
        raise Exception(f"訂單 {order_no} 缺少服務金額，無法產生通知")
    result = {
        "order_no": block["order_no"], "all_order_nos": [block["order_no"]],
        "address": address, "date": service_date, "period": service_time,
        "period_s": service_time, "actual_period": actual_time, "combined_period": "",
        "person": person_extracted, "service_amount": service_amount,
        "price_with_tax": service_amount, "fare": fare, "payway": payway,
        "region": region, "env_name": env_name, "session": session,
        "source_url": f"{base_url}/purchase?orderNo={block['order_no']}",
    }
    return result, build_line_message(result)


def build_combined_line_message_from_order_nos(env_name, backend_email, backend_password, order_nos, fallback_region="台北"):
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
        orders_info.append({"order_no": ono, "service_date": service_date, "period_s": service_time, "actual_period": actual_time, "person": person_extracted, "address": address, "fare": fare, "payway": payway, "service_amount": service_amount, "region": region})
    payways = {o["payway"] for o in orders_info if o["payway"]}
    if len(payways) > 1:
        raise Exception(f"合併的訂單付款方式不同（{', '.join(payways)}），請分開輸入分別產生通知。")
    unique_dates = sorted({o["service_date"] for o in orders_info})
    all_same_date = len(unique_dates) == 1
    if all_same_date:
        combined_period = _build_combined_period_display([{"period_s": o["period_s"], "actual_period": o["actual_period"], "person": o["person"]} for o in orders_info])
        multi_date = False
    else:
        period_lines = []
        for o in orders_info:
            d = o["service_date"].replace("-", "/")
            p_str = _format_period_display(str(o["period_s"] or "").replace(" ", ""), str(o["person"] or ""), display_override=str(o["actual_period"] or "").replace(" ", ""))
            period_lines.append(f"{d} {p_str}")
        combined_period = "\n".join(period_lines)
        multi_date = True
    amount_parts = []
    total_amount = 0
    for o in orders_info:
        try:
            v = int(str(o["service_amount"] or "0").replace(",", ""))
            amount_parts.append(str(v))
            total_amount += v
        except Exception:
            pass
    amount_display = "＋".join(amount_parts) + "＝" + str(total_amount) if len(amount_parts) > 1 else (str(total_amount) if total_amount else "")
    total_fare = 0
    for o in orders_info:
        try:
            total_fare += int(str(o["fare"] or "0").replace(",", ""))
        except Exception:
            pass
    first = orders_info[0]
    result = {
        "order_no": first["order_no"], "all_order_nos": order_nos,
        "address": first["address"], "date": first["service_date"],
        "period": first["period_s"], "period_s": first["period_s"],
        "actual_period": first["actual_period"], "combined_period": combined_period,
        "multi_date": multi_date, "person": first["person"],
        "service_amount": amount_display, "price_with_tax": str(total_amount),
        "fare": str(total_fare) if total_fare else "0", "payway": first["payway"],
        "region": first["region"], "env_name": env_name, "session": session,
        "source_url": f"{base_url}/purchase?orderNo={first['order_no']}",
    }
    return result, build_line_message(result)


# =========================================================
# 需求搜尋 / 其他工具
# =========================================================

def _is_target_day(d, day_type="不限"):
    weekday = d.weekday()
    if day_type == "平日":
        return weekday < 5
    if day_type == "週末":
        return weekday >= 5
    return True


def _filter_periods_by_preference(periods, time_preference="不限"):
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
    env_name, payway, lookup_result, address, clean_type_id, start_date,
    days=30, day_type="不限", time_preference="不限", plans=None,
    periods=None, period_hours=None, max_results=30,
):
    if isinstance(start_date, datetime):
        cursor = start_date.date()
    elif isinstance(start_date, date):
        cursor = start_date
    else:
        cursor = datetime.strptime(str(start_date), "%Y-%m-%d").date()
    periods = periods or ["08:30-12:30", "09:00-11:00", "09:00-12:00", "14:00-16:00", "14:00-17:00", "14:00-18:00", "09:00-16:00", "09:00-18:00"]
    period_hours = period_hours or {"08:30-12:30": 4, "09:00-11:00": 2, "09:00-12:00": 3, "14:00-16:00": 2, "14:00-17:00": 3, "14:00-18:00": 4, "09:00-16:00": 6, "09:00-18:00": 8}
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
                env_name=env_name, payway=payway, lookup_result=lookup_result,
                address=address, clean_type_id=clean_type_id, date_s=date_s,
                hour=target_hour, person=plan.get("person"),
                periods=target_periods, period_hours=period_hours,
            )
            for row in rows:
                if not row.get("available"):
                    continue
                results.append({"date": date_s, "period": row.get("period"), "person": plan.get("person"), "hour": target_hour, "total_person_hours": plan.get("total_person_hours"), "staff": row.get("staff", "")})
                if len(results) >= int(max_results):
                    return results
    return results


def get_stored_value(env_name, backend_email, backend_password, phone, clean_type_id="1"):
    base_url = _configure_environment(env_name)
    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗")
    page = session.get(f"{base_url}/booking/stored_value_routine", headers=HEADERS, allow_redirects=True)
    token_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
    csrf = token_m.group(1) if token_m else ""
    ajax = session.post(f"{base_url}/ajax/get_member", data={"phone": str(phone).strip(), "_token": csrf, "clean_type_id": str(clean_type_id)}, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"}, allow_redirects=True)
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
    try:
        d = datetime.strptime(str(date_text), "%Y-%m-%d").date()
    except Exception:
        return "平日"
    return "週末" if d.weekday() >= 5 else "平日"


def calc_stored_value_plan(sv, new_service_price=None, day_type="平日", total_person_hours=None, zero_total_stored_order=True):
    import math
    sv = int(float(sv or 0))
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
    coupon_a = max(dummy_price - sv, 0)
    coupon_b = sv
    customer_pays = (new_service_price - sv) if new_service_price else None
    return {"unit_price": unit_price, "dummy_price": dummy_price, "coupon_a": coupon_a, "coupon_b": coupon_b, "customer_pays": customer_pays, "n": n, "total_person_hours": ph or n, "stored_value_applied": min(sv, dummy_price), "stored_order_total_after_coupon": max(dummy_price - coupon_a - sv, 0), "zero_total_stored_order": bool(zero_total_stored_order)}


def _invoice_payload(invoice_mode, member_email="", mobile_carrier="", company_title="", company_no=""):
    mode = str(invoice_mode or "會員載具").strip()
    if mode == "手機載具":
        if not str(mobile_carrier or "").strip().startswith("/"):
            raise Exception("手機載具需以 / 開頭")
        return {"invoice_type_override": "2", "carrier_type_id_override": "2", "carrier_info": str(mobile_carrier).strip(), "company_title": "", "company_no": "", "payment_type": "B2C"}
    if mode == "三聯式":
        if not str(company_title or "").strip() or not str(company_no or "").strip():
            raise Exception("三聯式發票需填寫抬頭與統編")
        return {"invoice_type_override": "3", "carrier_type_id_override": "1", "carrier_info": "", "company_title": str(company_title).strip(), "company_no": str(company_no).strip(), "payment_type": "B2B"}
    return {"invoice_type_override": "2", "carrier_type_id_override": "1", "carrier_info": str(member_email or "").strip(), "company_title": "", "company_no": "", "payment_type": "B2C"}


def _stored_value_makeup_context(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date,
    period_s, hour, person, address="", region="", coupon_prefix_base="",
    coupon_valid_days=60, balance_override=None, allow_zero_balance=False,
):
    day_type = _day_type_from_date(service_date)
    if balance_override not in (None, ""):
        sv = int(float(balance_override))
    else:
        sv, _ = get_stored_value(env_name, backend_email, backend_password, phone, clean_type_id)
    if sv <= 0 and not allow_zero_balance:
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
    return {"balance": sv, "plan": plan, "lookup": lookup, "member": member, "address": selected_address, "region": selected_region, "day_type": day_type, "today_str": today_str, "date_e": date_e, "prefix_a": f"svA{suffix}", "prefix_b": f"svB{suffix}"}


def stored_value_makeup_create_stored_order(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
    hour, person, address="", region="", coupon_prefix_base="", coupon_valid_days=60,
):
    ctx = _stored_value_makeup_context(env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s, hour, person, address, region, coupon_prefix_base, coupon_valid_days)
    regions = [ctx["region"]] if ctx.get("region") else list(COUPON_COMPANY_ID_MAP.keys())
    services = ["居家清潔", "裝修細清"]
    coupon_a = create_coupon(env_name, backend_email, backend_password, title=f"儲值金清零-{phone}", discount=ctx["plan"]["coupon_a"], date_s=ctx["today_str"], date_e=ctx["date_e"], prefix=ctx["prefix_a"], piece="1", regions=regions, service_items=services)
    code_a = coupon_a.get("coupon_code") or coupon_a.get("coupon_prefix") or ctx["prefix_a"]
    stored_order = quick_create_order(env_name=env_name, payway="儲值金", region=ctx["region"], lookup_result=ctx["lookup"], address=ctx["address"], clean_type_id=clean_type_id, date_s=service_date, period_s=period_s, hour=str(hour), person=str(person), discount_code=code_a)
    lemon_result = assign_lemon_cleaners_to_order(session=stored_order["session"], base_url=_configure_environment(env_name), order_no_a=stored_order["order_no"], service_date=service_date, period_s=period_s, person_count=str(person))
    note = (f"儲值金補價差第一段：儲值金折抵單 {stored_order['order_no']}，{ctx['day_type']}單價 {ctx['plan']['unit_price']} × {ctx['plan']['total_person_hours']}人時 = {ctx['plan']['dummy_price']}，優惠券A折抵 {ctx['plan']['coupon_a']} 元，剩餘 {ctx['plan']['stored_value_applied']} 元扣儲值金後總額應為 0，檸檬人勿動。")
    _update_order_note(stored_order["session"], _configure_environment(env_name), stored_order["order_no"], note)
    return {"stage": "stored_order", "balance": ctx["balance"], "plan": ctx["plan"], "day_type": ctx["day_type"], "coupon_a": coupon_a, "stored_order": stored_order, "lemon_result": lemon_result, "note": note, "address": ctx["address"], "region": ctx["region"], "phone": phone, "clean_type_id": clean_type_id, "service_date": service_date, "period_s": period_s, "hour": str(hour), "person": str(person), "coupon_prefix_base": coupon_prefix_base or phone, "coupon_valid_days": coupon_valid_days}


def stored_value_makeup_create_paid_order(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
    hour, person, customer_payway="ATM", invoice_mode="會員載具", mobile_carrier="",
    company_title="", company_no="", address="", region="", coupon_prefix_base="",
    coupon_valid_days=60, stored_order_no="", balance_override=None,
):
    ctx = _stored_value_makeup_context(env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s, hour, person, address, region, coupon_prefix_base, coupon_valid_days, balance_override=balance_override)
    if balance_override not in (None, ""):
        ctx["balance"] = int(float(balance_override))
        ctx["plan"]["coupon_b"] = ctx["balance"]
    regions = [ctx["region"]] if ctx.get("region") else list(COUPON_COMPANY_ID_MAP.keys())
    services = ["居家清潔", "裝修細清"]
    coupon_b = create_coupon(env_name, backend_email, backend_password, title=f"儲值金補價差客付-{phone}", discount=ctx["plan"]["coupon_b"], date_s=ctx["today_str"], date_e=ctx["date_e"], prefix=ctx["prefix_b"], piece="1", regions=regions, service_items=services)
    code_b = coupon_b.get("coupon_code") or coupon_b.get("coupon_prefix") or ctx["prefix_b"]
    invoice = _invoice_payload(invoice_mode, member_email=ctx["member"].get("email") or "", mobile_carrier=mobile_carrier, company_title=company_title, company_no=company_no)
    paid_order = quick_create_order(env_name=env_name, payway=customer_payway, region=ctx["region"], lookup_result=ctx["lookup"], address=ctx["address"], clean_type_id=clean_type_id, date_s=service_date, period_s=period_s, hour=str(hour), person=str(person), discount_code=code_b, **invoice)
    pair = f"儲值折抵單 {stored_order_no} + 客付補價差單 {paid_order['order_no']}" if stored_order_no else f"客付補價差單 {paid_order['order_no']}"
    note = f"儲值金補價差第二段：{pair}，客付單使用優惠券B折抵原儲值金餘額 {ctx['balance']} 元。"
    _update_order_note(paid_order["session"], _configure_environment(env_name), paid_order["order_no"], note)
    return {"stage": "paid_order", "balance": ctx["balance"], "plan": ctx["plan"], "day_type": ctx["day_type"], "coupon_b": coupon_b, "paid_order": paid_order, "note": note, "line_message": build_line_message(paid_order), "address": ctx["address"], "region": ctx["region"], "stored_order_no": stored_order_no}


def stored_value_makeup_convert(
    env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s,
    hour, person, day_type="", customer_payway="ATM", invoice_mode="會員載具",
    mobile_carrier="", company_title="", company_no="", address="", region="",
    coupon_prefix_base="", coupon_valid_days=60,
):
    first = stored_value_makeup_create_stored_order(env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s, hour, person, address, region, coupon_prefix_base, coupon_valid_days)
    second = stored_value_makeup_create_paid_order(env_name, backend_email, backend_password, phone, clean_type_id, service_date, period_s, hour, person, customer_payway, invoice_mode, mobile_carrier, company_title, company_no, first["address"], first["region"], coupon_prefix_base, coupon_valid_days, stored_order_no=first["stored_order"].get("order_no", ""), balance_override=first["balance"])
    note = first.get("note", "") + "\n" + second.get("note", "")
    return {"balance": first["balance"], "plan": first["plan"], "day_type": first["day_type"], "coupon_a": first.get("coupon_a"), "coupon_b": second.get("coupon_b"), "stored_order": first.get("stored_order"), "paid_order": second.get("paid_order"), "lemon_result": first.get("lemon_result"), "note": note, "line_message": second.get("line_message"), "address": first["address"], "region": first["region"]}


def parse_new_customer_order_text(raw_text):
    text = str(raw_text or "").strip()
    result = {"name": "", "phone": "", "email": "", "address": "", "ping": "", "payway": "", "invoice_type": "", "invoice_title": "", "tax_id": "", "carrier": "", "requirement": "", "note": ""}
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
    requirement_patterns = [r"(平日|週末|假日|不限).*(\d+)\s*人\s*(\d+(?:\.\d+)?)\s*小時", r"(\d+)\s*人\s*(\d+(?:\.\d+)?)\s*小時"]
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
    required = ["name", "phone", "email", "address", "payway", "clean_type_id"]
    missing = [key for key in required if not str((customer or {}).get(key, "")).strip()]
    if missing:
        raise Exception("新客資料不足，請補齊：" + "、".join(missing))
    carrier = str((customer or {}).get("carrier", "")).strip()
    invoice_type = str((customer or {}).get("invoice_type", "")).strip()
    if invoice_type == "手機載具" and not carrier.startswith("/"):
        raise Exception("手機載具格式可能不正確，範例：/T8K346B")
    raise Exception("新客資料已完成前端收集與驗證；但目前 quick_order.py 的既有建單核心需要會員 addressId，尚未接上新客建立會員/地址或新客訂單送出 API。")
