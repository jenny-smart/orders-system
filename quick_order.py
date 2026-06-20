# -*- coding: utf-8 -*-
"""
單筆快速建單模組（信用卡 / ATM / 儲值金）

設計目的：
非定期、單次客人，不需要先把整列資料填進 Google Sheet。
電話查會員 → 帶出上次地址/服務內容/付款方式 → 算時數 → 直接建單 → 產生 LINE 文案。

直接 reuse orders.py 既有的後台互動 function，避免重複邏輯/重複維護。
"""
import time
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
    fetch_order_no_by_date_and_period,
    fetch_order_meta_by_order_no,
    extract_order_cards_from_purchase_html,
    send_confirmation_mail,
    normalize_phone,
    normalize_addr_for_match,
    display_period_text,
    first_nonzero,
    find_nested_value,
    HEADERS,
)
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


def list_order_numbers_for_phone(session, phone):
    """
    掃描訂單列表頁，只抓「電話符合」的訂單編號集合。
    用於送出建單前後比對，確認真的有新訂單產生，
    而不是 fetch_order_no_by_date_and_period 那種不限客人、
    撞到同日期同時段的舊訂單就誤判成功。
    """
    resp = session.get(orders.PURCHASE_URL, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return set()

    phone_norm = normalize_phone(phone)
    blocks = extract_order_cards_from_purchase_html(resp.text)

    result = set()
    for block in blocks:
        joined = "\n".join(block.get("lines", []))
        if phone_norm and phone_norm in joined.replace("-", "").replace(" ", ""):
            result.add(block["order_no"])
    return result


def get_last_service_summary(session, member_payload, address):
    """
    取得選定地址「上一次服務」摘要：日期/時段/服務人員/總人時，
    給畫面提示用，避免約錯人時或誤判上次服務人員。

    優先用 lastPurchase（若地址相符），否則退而求其次用該地址的 purchase 物件。
    若有 order_no，再去後台訂單列表撈實際服務日期/時間/服務人員（比較準確）。
    """
    if not isinstance(member_payload, dict):
        return None

    last_purchase = member_payload.get("lastPurchase", {}) or {}
    member = member_payload.get("member", {}) or {}
    addr_list = member.get("memberAddressList", []) or []

    target_norm = normalize_addr_for_match(address)
    matched = {}

    if last_purchase and normalize_addr_for_match(last_purchase.get("address", "")) == target_norm:
        matched = last_purchase
    else:
        for item in addr_list:
            if normalize_addr_for_match(item.get("address", "")) == target_norm:
                item_purchase = item.get("purchase", {})
                if isinstance(item_purchase, dict) and item_purchase:
                    matched = item_purchase
                break

    if not matched:
        return None

    person = matched.get("person", "")
    hour = matched.get("hour", "")
    order_no = str(matched.get("order_no", "") or "").strip()
    service_date = str(matched.get("date_clean", "") or "")
    service_time = ""
    staff = ""

    if order_no:
        try:
            meta = fetch_order_meta_by_order_no(session, order_no)
            staff = meta.get("服務人員", "") or ""
            service_time = meta.get("服務時間", "") or ""
            if meta.get("服務日期"):
                service_date = meta.get("服務日期")
        except Exception:
            pass

    return {
        "date": service_date,
        "time": service_time,
        "person": person,
        "hour": hour,
        "staff": staff,
        "order_no": order_no,
    }


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
    token = lookup_result["token"]
    member_payload = lookup_result["member_payload"]
    phone = lookup_result["phone"]

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
    if not addr_check:
        raise Exception(f"查詢地址/地區失敗：{selected_address}")

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

    booking_url = f"{base_url}{BOOKING_ENDPOINT_MAP.get(payway, '/booking/single')}"

    # 送出建單前，先記錄此電話目前有哪些訂單編號，
    # 用來在送出後判斷「是否真的產生新訂單」，
    # 避免撞到同日期同時段的舊訂單時被誤判成功。
    before_order_nos = list_order_numbers_for_phone(session, phone)

    session.post(
        booking_url,
        data={**base_data, "_token": token, "date_list[]": [slot]},
        headers=HEADERS,
        allow_redirects=True,
    )
    time.sleep(1)

    after_order_nos = list_order_numbers_for_phone(session, phone)
    new_order_nos = after_order_nos - before_order_nos

    display_period = display_period_text(period_s.split("-")[0], period_s.split("-")[1])

    if not new_order_nos:
        raise Exception(
            "建單失敗：系統未產生新訂單編號（可能該客人此時段已有訂單存在，或後台拒絕重複預約）。"
            "請至後台『訂單管理』手動確認，不要直接使用畫面上顯示的舊訂單資訊。"
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
    period = order_result["period"]
    price = order_result.get("price_with_tax", order_result.get("price"))
    fare = order_result["fare"]
    address = order_result["address"]
    order_no = order_result["order_no"]
    order_last6 = order_no[-6:] if len(order_no) >= 6 else order_no

    common_footer = """＊若現場溝通時確認無法於服務時間內完成服務需求，會請您排優先順序，以時間內可以完成的區域為主。
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
車馬費：{fare}
檸檬家事專員會於現場再溝通服務需求，
以於系統估算時間內可以完的服務項目為主。
預約完成後，即代表您同意接受檸檬專業清潔公司 服務條款 及 隱私權政策。
請詳閱服務條款及隱私權相關說明 https://www.lemonclean.com.tw/terms
建議您可以至會員中心》訂單查詢 確認喔
https://www.lemonclean.com.tw/login
帳號：email；密碼：手機號碼
＊即日起本站暫停做防疫調查，為保障客戶及專員安全，若確診請於服務前日主動告知，否則需付異動費喔
若訂購後有上述情事請主動連繫檸檬家事官方LINE@，謝謝。"""

    if payway == "信用卡":
        return f"""感謝您於 檸檬家事 預約【居家清潔】服務！
服務時間 : {date_disp}  {period}
服務金額：{price}（含稅）
車馬費： {fare}   (服務完後收取)
服務地址：{address}
※麻煩您於『明天 24:00前』完成付款，為保留他人訂購權利，逾期付款訂單將自動取消
**當您完成付款後即表示服務已完成預約，
預約完成後，即代表您同意接受檸檬專業清潔公司 服務條款 及 隱私權政策。
請詳閱服務條款及隱私權相關說明 https://www.lemonclean.com.tw/terms
{common_footer}
線上刷卡流程:
[https://www.lemonclean.com.tw/order/](https://www.lemonclean.com.tw/order/{order_no}){order_last6}
登入會員
帳號：email；密碼：手機號碼
在訂單點選付款狀態點選『重新付款』即可
{cancel_policy}"""

    if payway == "ATM":
        if region == "台北":
            bank_block = """銀行戶名：檸檬專業清潔有限公司
銀行代碼 台北富邦銀行(012)-松高分行
銀行帳號 7091-2000-3320"""
            extra_note = (
                "*發票於付款完成後24小時之內會開立並寄至Email，屆時麻煩查收或是檢查垃圾郵件。\n"
                "*匯款完成後再請您提供您的匯款帳號後5碼，以供檸檬家事為您核對帳款。\n"
            )
        else:
            # 台中帳戶；其餘區域（桃園/新竹/高雄）目前沿用台中帳戶，
            # 若日後各區開獨立帳戶，這裡再依 region 擴充對照表。
            bank_block = """銀行戶名：泳檬有限公司
銀行代碼 台北富邦銀行(012)-營業部
銀行帳號 00200102520512"""
            extra_note = ""

        return f"""感謝您於 檸檬家事 預約【居家清潔】服務！
服務時間 : {date_disp}  {period}
服務地址：{address}
車馬費：{fare}
※麻煩您於『明天 24:00前』完成付款，為保留他人訂購權利，逾期付款訂單將自動取消
**當您完成付款後即表示服務已完成預約，
預約完成後，即代表您同意接受檸檬專業清潔公司 服務條款 及 隱私權政策。
請詳閱服務條款及隱私權相關說明 https://www.lemonclean.com.tw/terms
{common_footer}
▲請您依下列匯款帳戶資訊繳費，謝謝！
{bank_block}
轉帳金額  {price}元（含營業稅）
訂單可以登入『會員中心』查詢確認
https://www.lemonclean.com.tw/login
帳號：email；密碼：手機號碼
{extra_note}{cancel_policy}"""

    raise Exception(f"未知付款方式: {payway}")
