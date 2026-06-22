# ============================================================
# 檔名：ordersapp_7_3.py
# 版本：v7.3
# 模組：服務訂單系統主畫面
# 建立日期：2026-06-22
# 最後更新：2026-06-22
#
# Change Log
# v7.3
# - LINE 通知產生器：訂單編號改多筆輸入（text_area，每行一個）
# - LINE 通知產生器：移除區域選擇欄位，由地址自動判斷
# - LINE 通知產生器：每筆訊息旁增加 N-J Memo 欄位 + 複製按鈕
# v7.2
# - 新客資料拆解電話欄位統一只保留數字
# - 新增發票抬頭與統編拆解欄位
# - 整理後文字同步輸出抬頭/統編，方便貼到後台
# - LINE通知移除區域選擇，由訂單資料自動判斷
# ============================================================
# -*- coding: utf-8 -*-
import html
import json
import streamlit as st
import streamlit.components.v1 as components
from datetime import date, timedelta

from orders import run_process_web, get_region_by_address
from accounts import ACCOUNTS
from quick_order import (
    quick_lookup_member,
    quick_create_order,
    quick_check_available_slots,
    send_confirmation,
    build_line_message,
    build_line_message_from_order_no,
    build_combined_line_message_from_order_nos,
    get_last_paid_summary,
    get_last_paid_per_address,
    get_unserved_paid_orders,
    get_last_purchase_fetch_debug,
    build_equivalent_plans,
    search_available_service_dates,
    parse_new_customer_order_text,
    create_coupon,
    COUPON_COMPANY_ID_MAP,
    COUPON_SERVICE_ITEM_MAP,
    COUPON_TYPE_MAP,
)

st.set_page_config(page_title="服務訂單系統", page_icon="🧹", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');

:root {
    --lemon: #F5C518;
    --lemon-dark: #D4A017;
    --lemon-soft: #FFFBEA;
    --lemon-mid: #FFF3C4;
    --charcoal: #1C1C1E;
    --ink: #3A3A3C;
    --muted: #8E8E93;
    --border: #E5E5EA;
    --surface: #FFFFFF;
    --success: #34C759;
    --danger: #FF3B30;
    --radius: 14px;
}

html, body, [class*="css"] {
    font-family: 'Noto Sans TC', sans-serif;
    color: var(--charcoal);
}

#MainMenu, footer, header {
    visibility: hidden;
}

[data-testid="stAppViewContainer"] {
    background: #FAFAFA;
}

.block-container {
    padding-top: 2rem !important;
    padding-bottom: 2rem !important;
    max-width: 1180px !important;
}

.hero {
    background: linear-gradient(135deg, #FFFDF0 0%, #FFFBEA 100%);
    border: 1.5px solid var(--lemon-mid);
    border-radius: var(--radius);
    padding: 2rem 2.5rem 1.6rem;
    margin-bottom: 2rem;
    display: flex;
    align-items: center;
    gap: 1.2rem;
    box-shadow: 0 2px 12px rgba(245,197,24,0.10);
}

.hero-emoji {
    font-size: 3rem;
    line-height: 1;
}

.hero-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--charcoal);
    letter-spacing: -0.5px;
}

.hero-sub {
    color: var(--ink);
    font-size: 0.92rem;
    margin-top: 0.3rem;
    opacity: 0.78;
}

.step-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    background: var(--lemon-mid);
    border: 1.5px solid var(--lemon);
    border-radius: 30px;
    padding: 0.28rem 0.9rem;
    font-size: 0.78rem;
    font-weight: 700;
    color: var(--charcoal);
    margin-bottom: 0.9rem;
    letter-spacing: 0.02em;
}

.step-num {
    background: var(--lemon);
    border-radius: 50%;
    width: 20px;
    height: 20px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.72rem;
    font-weight: 700;
}

.sec-label {
    font-size: 12px;
    font-weight: 700;
    color: var(--muted);
    letter-spacing: .04em;
    margin-bottom: 8px;
}

.hint-box {
    background: var(--lemon-soft);
    border-left: 4px solid var(--lemon);
    border-radius: 0 8px 8px 0;
    padding: 0.75rem 1rem;
    font-size: 0.9rem;
    color: var(--ink);
    margin-top: 0.6rem;
}

[data-testid="stTextInput"] label,
[data-testid="stNumberInput"] label,
[data-testid="stSelectbox"] label,
[data-testid="stMultiSelect"] label,
[data-testid="stDateInput"] label,
[data-testid="stRadio"] label {
    font-size: 13px !important;
    color: var(--ink) !important;
    font-weight: 700 !important;
}

[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stSelectbox"] > div > div,
[data-testid="stMultiSelect"] > div > div,
[data-testid="stDateInput"] input {
    border-radius: 10px !important;
    border: 1.5px solid var(--border) !important;
    background: white !important;
    font-size: 15px !important;
}

[data-testid="stTextInput"] input:focus {
    border-color: var(--lemon-dark) !important;
    box-shadow: 0 0 0 2px rgba(245,197,24,0.22) !important;
}

[data-testid="stButton"] > button {
    background: var(--lemon) !important;
    color: var(--charcoal) !important;
    border: none !important;
    border-radius: 10px !important;
    font-size: 15px !important;
    font-weight: 700 !important;
    padding: 0.55rem 1.2rem !important;
    box-shadow: 0 2px 10px rgba(245,197,24,0.28) !important;
}

[data-testid="stButton"] > button:hover {
    background: var(--lemon-dark) !important;
    transform: translateY(-1px) !important;
}

[data-testid="stButton"] > button:disabled {
    background: #D1D5DB !important;
    color: #777 !important;
}

[data-testid="stExpander"] {
    border: 1px solid #ececec !important;
    border-radius: 14px !important;
    background: white !important;
    overflow: hidden !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.04);
}

[data-testid="stExpander"] summary {
    font-size: 14px !important;
    font-weight: 700 !important;
    color: var(--charcoal) !important;
    padding: 12px 16px !important;
}

[data-testid="stCode"] {
    font-size: 13px !important;
    border-radius: 0 0 12px 12px !important;
    min-height: 420px !important;
    max-height: 560px !important;
    overflow-y: auto !important;
    background: #1C1C1E !important;
    margin: 0 !important;
    white-space: pre-wrap !important;
}

[data-testid="stMetric"] {
    background: white !important;
    border: 1px solid #ececec !important;
    border-radius: 14px !important;
    padding: 14px 16px !important;
    text-align: center !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.04);
}

[data-testid="stMetricLabel"] {
    font-size: 12px !important;
    color: var(--muted) !important;
    font-weight: 700 !important;
}

[data-testid="stMetricValue"] {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 32px !important;
    font-weight: 700 !important;
    color: var(--charcoal) !important;
}

[data-testid="stAlert"] {
    border-radius: 10px !important;
    font-size: 14px !important;
}

hr {
    border-color: #e8e8e8 !important;
    margin: 1.4rem 0 !important;
}

.history-card {
    background: var(--lemon-soft);
    border-left: 4px solid var(--lemon);
    border-radius: 0 10px 10px 0;
    padding: 1rem 1.1rem;
    margin-top: 0.85rem;
    font-size: 0.94rem;
    color: var(--ink);
}

.history-title {
    font-size: 1rem;
    font-weight: 800;
    color: var(--charcoal);
    margin-bottom: 0.75rem;
}

.history-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.45rem 1.2rem;
}

.history-field {
    display: grid;
    grid-template-columns: 5.5rem minmax(0, 1fr);
    gap: 0.35rem;
    align-items: start;
}

.history-label {
    color: var(--muted);
    font-weight: 700;
    white-space: nowrap;
}

.history-value {
    color: var(--charcoal);
    font-weight: 600;
    overflow-wrap: anywhere;
}

.history-subtitle {
    margin-top: 0.9rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--lemon-mid);
    font-weight: 800;
    color: var(--charcoal);
}

.history-order {
    margin-top: 0.55rem;
    padding: 0.65rem 0.75rem;
    background: rgba(255,255,255,0.58);
    border: 1px solid var(--lemon-mid);
    border-radius: 8px;
}

.history-order-main {
    font-weight: 800;
    color: var(--charcoal);
    margin-bottom: 0.35rem;
}

.history-order-meta {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.25rem 1rem;
    color: var(--ink);
}

.history-note {
    margin-top: 0.75rem;
    color: var(--muted);
}

@media (max-width: 720px) {
    .history-grid,
    .history-order-meta {
        grid-template-columns: 1fr;
    }
}
</style>
""", unsafe_allow_html=True)


CLEAN_TYPE_ID_MAP = {"居家清潔": "1", "辦公室清潔": "2", "裝修細清": "3"}

PERIOD_OPTIONS = [
    "08:30-12:30",
    "09:00-11:00",
    "09:00-12:00",
    "14:00-16:00",
    "14:00-17:00",
    "14:00-18:00",
    "09:00-16:00",
    "09:00-18:00",
]

PERIOD_HOUR_MAP = {
    "08:30-12:30": 4,
    "09:00-11:00": 2,
    "09:00-12:00": 3,
    "14:00-16:00": 2,
    "14:00-17:00": 3,
    "14:00-18:00": 4,
    "09:00-16:00": 6,
    "09:00-18:00": 8,
}

# N-J Memo 固定文案
NJ_MEMO = (
    "**N-J**\n"
    "請現場跟客戶溝通清潔優先順序,並請回報以下內容\n"
    "*工作項目+時間分配\n"
    "*特別注意事項\n"
    "*服務小貼心"
)


def compact_period(value):
    return str(value or "").replace(" ", "")


def nonzero_money(value):
    try:
        return float(str(value or "0").replace(",", "")) != 0
    except Exception:
        return bool(str(value or "").strip())


def payment_invoice_display(payway, invoice_text):
    if payway == "儲值金":
        return "儲值金客（無付款方式/發票資訊）"
    return f"付款：{payway or '未知'}　發票：{invoice_text or '未知'}"


def booking_route_display(payway):
    if payway == "儲值金":
        return "儲值金客", "/booking/stored_value_routine"
    return "一般客", "/booking/single"


def h(value, default="未知"):
    text = str(value or "").strip()
    return html.escape(text if text else default)


def person_hour_display(person, hour):
    return f"{person}人{hour}小時" if (person or hour) else "未知"


def history_field(label, value):
    return (
        '<div class="history-field">'
        f'<span class="history-label">{h(label, "")}</span>'
        f'<span class="history-value">{h(value)}</span>'
        '</div>'
    )


def order_history_row(order):
    ph_text = person_hour_display(order.get("person"), order.get("hour"))
    payment_text = payment_invoice_display(order.get("payway"), order.get("invoice_text"))
    notice = order.get("service_notice") or "無"
    fare = order.get("fare") or ""
    fare_part = f'<div>車馬費：{h(fare, "")}</div>' if nonzero_money(fare) else ""
    return (
        '<div class="history-order">'
        f'<div class="history-order-main">{h(order.get("order_no"))}　{h(order.get("date"))} {h(order.get("time"), "")}</div>'
        '<div class="history-order-meta">'
        f'<div>人時：{h(ph_text)}</div>'
        f'<div>服務人員：{h(order.get("staff"))}</div>'
        f'<div>地址：{h(order.get("address"))}</div>'
        f'<div>{h(payment_text)}</div>'
        f'<div>客服備註：{h(notice)}</div>'
        f'{fare_part}'
        '</div>'
        '</div>'
    )


def last_summary_card_html(summary):
    ph_text = person_hour_display(summary.get("person"), summary.get("hour"))
    payment_text = payment_invoice_display(summary.get("payway"), summary.get("invoice_text"))
    fields = [
        ("訂單", summary.get("order_no")),
        ("服務時間", f'{summary.get("date") or ""} {summary.get("time") or ""}'.strip()),
        ("地址", summary.get("address") or "無法判斷地址"),
        ("類別", summary.get("clean_type")),
        ("服務人員", summary.get("staff")),
        ("人時", ph_text),
        ("付款/發票", payment_text),
        ("客服備註", summary.get("service_notice") or "無"),
    ]
    if nonzero_money(summary.get("fare")):
        fields.append(("車馬費", summary.get("fare")))

    same_date_orders = summary.get("same_date_orders") or []
    same_date_html = ""
    if len(same_date_orders) > 1:
        same_date_html = (
            f'<div class="history-subtitle">該日期共有 {len(same_date_orders)} 筆已付款訂單</div>'
            + "".join(order_history_row(order) for order in same_date_orders)
        )

    return (
        '<div class="history-card">'
        '<div class="history-title">📌 上次（已付款）服務</div>'
        '<div class="history-grid">'
        + "".join(history_field(label, value) for label, value in fields)
        + '</div>'
        + same_date_html
        + '<div class="history-note">以上已預設帶入，如有變動請手動調整對應欄位。</div>'
        + '</div>'
    )


def copy_button(label, text, key):
    payload = json.dumps(text, ensure_ascii=False)
    label_payload = json.dumps(label, ensure_ascii=False)
    components.html(
        f"""
        <button id="{key}" style="
            width: 100%;
            padding: 0.65rem 1rem;
            border: 0;
            border-radius: 10px;
            background: #F5C518;
            color: #1C1C1E;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
        ">{html.escape(label)}</button>
        <script>
        const btn = document.getElementById({json.dumps(key)});
        const text = {payload};
        const label = {label_payload};
        btn.addEventListener("click", async () => {{
            try {{
                await navigator.clipboard.writeText(text);
                btn.textContent = "已複製";
            }} catch (err) {{
                const ta = document.createElement("textarea");
                ta.value = text;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand("copy");
                document.body.removeChild(ta);
                btn.textContent = "已複製";
            }}
            setTimeout(() => {{ btn.textContent = label; }}, 1600);
        }});
        </script>
        """,
        height=54,
    )


def step(num, title):
    st.markdown(
        f'<div class="step-pill"><span class="step-num">{num}</span>{title}</div>',
        unsafe_allow_html=True
    )


def info_panel(title, bullets):
    items = "".join(f"<li>{html.escape(str(item))}</li>" for item in bullets)
    st.markdown(
        f'<div class="hint-box"><b>{html.escape(str(title))}</b><ul style="margin:0.45rem 0 0 1.1rem; padding:0;">{items}</ul></div>',
        unsafe_allow_html=True,
    )


def parse_row_input(row_text: str):
    if not row_text or not row_text.strip():
        raise ValueError("請輸入列號，例如：2,3,5-7")

    rows = set()

    for part in [p.strip() for p in row_text.split(",") if p.strip()]:
        if "-" in part:
            s, e = part.split("-", 1)
            s, e = int(s.strip()), int(e.strip())

            if s <= 0 or e <= 0:
                raise ValueError("列號必須大於 0")
            if s > e:
                raise ValueError(f"區間錯誤：{part}")

            rows.update(range(s, e + 1))
        else:
            n = int(part)
            if n <= 0:
                raise ValueError("列號必須大於 0")
            rows.add(n)

    return sorted(rows)


def format_log_message(msg):
    text = str(msg)

    text = text.replace("\\n", "\n")
    text = text.replace("目前環境：", "\n目前環境：")
    text = text.replace("BASE_URL：", "\nBASE_URL：")
    text = text.replace("執行區域：", "\n執行區域：")
    text = text.replace("執行工作表：", "\n執行工作表：")
    text = text.replace("執行列範圍：", "\n執行列範圍：")
    text = text.replace("處理第", "\n處理第")
    text = text.replace("已回填 Google Sheet。", "\n已回填 Google Sheet。")

    if text.startswith("▶"):
        text = "\n" + text

    return text.strip()


st.markdown("""
<div class="hero">
  <div class="hero-emoji">🧹</div>
  <div>
    <div class="hero-title">服務訂單系統</div>
    <div class="hero-sub">支援批次建單、舊客快速建單、新客資料拆解、LINE 通知、確認信與 Google 日曆同步。</div>
  </div>
</div>
""", unsafe_allow_html=True)

step("1", "登入與環境設定")

col_e, col_p, col_env = st.columns([3.2, 3.2, 1.2])
with col_e:
    backend_email = st.text_input("後台帳號")
with col_p:
    backend_password = st.text_input("後台密碼", type="password")
with col_env:
    env = st.selectbox("環境", ["prod", "dev"], index=0)

st.markdown("<hr>", unsafe_allow_html=True)

step("2", "功能選單")
info_panel(
    "功能說明",
    [
        "批次建單：從 Google Sheet 逐列建立訂單、寄確認信、同步 Google 日曆。",
        "舊客快速建單：用電話查會員，帶入歷史已付款服務資料後建單；需求搜尋整合在此流程內。",
        "新客資料拆解：貼上客人提供的制式文字，系統拆成欄位供客服修改與複製，不直接送單。",
        "LINE 通知產生器：用已成立訂單編號補產生通知訊息，支援多筆同時產生。",
    ],
)
mode = st.radio(
    "功能選單",
    ["批次建單（Google Sheet）", "舊客快速建單", "新客資料拆解", "LINE 通知產生器", "優惠券建立"],
    horizontal=True,
)

st.markdown("<hr>", unsafe_allow_html=True)


# =========================================================
# 模式一：批次建單（Google Sheet）
# =========================================================
if mode == "批次建單（Google Sheet）":

    step("3", "批次建單")
    info_panel(
        "功能說明",
        [
            "適合已將多筆訂單整理在 Google Sheet 的批次處理情境。",
            "可依列號建立訂單、寄確認信、改 Google 日曆，並回填結果。",
        ],
    )
    info_panel(
        "使用說明",
        [
            "先選擇執行區域與工作表名稱。",
            "輸入要執行的列號，例如 2、2,3,5 或 5-10。",
            "勾選要執行的項目後按開始執行。",
        ],
    )

    step("4", "執行設定")

    c1, c2, c3 = st.columns(3)
    with c1:
        region = st.selectbox("執行區域", ["台北", "台中", "桃園", "新竹", "高雄"])
    with c2:
        sheet_name = st.text_input("工作表名稱", value="", placeholder="例：202604")
    with c3:
        row_input = st.text_input("執行列號", value="", placeholder="例：2,3,5-7")

    st.markdown(
        '<div class="hint-box">💡 列號支援：單列 <code>2</code>、逗號分隔 <code>2,3,5</code>、區間 <code>2,3,5-7</code></div>',
        unsafe_allow_html=True
    )

    st.markdown("<hr>", unsafe_allow_html=True)

    step("3", "執行項目")

    default_actions = (
        ["建單", "寄確認信", "改 Google 日曆"]
        if env == "prod"
        else ["建單"]
    )

    selected_actions = st.multiselect(
        "執行項目",
        options=["建單", "寄確認信", "改 Google 日曆"],
        default=default_actions,
        label_visibility="collapsed",
    )

    st.markdown(
        '<div class="hint-box">可自由組合，例如只寄確認信、只改日曆，或全流程一起跑。</div>',
        unsafe_allow_html=True
    )

    st.markdown("<hr>", unsafe_allow_html=True)

    run_clicked = st.button("🚀  開始執行", use_container_width=True)

    with st.expander("📄  執行過程", expanded=True):
        log_box = st.empty()
        log_box.code("尚未執行")

    result_container = st.container()

    if run_clicked:
        if not backend_email.strip():
            st.error("請輸入後台帳號")
            st.stop()
        if not backend_password.strip():
            st.error("請輸入後台密碼")
            st.stop()
        if not sheet_name.strip():
            st.error("請輸入工作表名稱")
            st.stop()
        if not selected_actions:
            st.error("請至少選擇一個執行項目")
            st.stop()

        try:
            target_rows = parse_row_input(row_input)
        except Exception as e:
            st.error(f"列號格式錯誤：{e}")
            st.stop()

        logs = []

        def ui_log(msg):
            logs.append(format_log_message(msg))
            display_text = "\n\n".join(logs[-120:])
            log_box.code(display_text)

        total_success = 0
        total_fail = 0
        total_processed = 0

        with st.spinner("執行中，請稍候…"):
            for row_no in target_rows:
                ui_log(f"▶ 開始執行第 {row_no} 列…")

                try:
                    result = run_process_web(
                        env_name=env,
                        region=region,
                        backend_email=backend_email.strip(),
                        backend_password=backend_password.strip(),
                        sheet_name=sheet_name.strip(),
                        start_row=row_no,
                        end_row=row_no,
                        selected_actions=selected_actions,
                        logger=ui_log,
                    )

                    if isinstance(result, dict):
                        total_success += result.get("success_count", 0)
                        total_fail += result.get("fail_count", 0)
                        total_processed += result.get("total_processed", 0)

                except Exception as e:
                    total_fail += 1
                    ui_log(f"❌ 第 {row_no} 列失敗：{e}")

        ui_log("===== 執行完成 =====")

        with result_container:
            st.markdown("<hr>", unsafe_allow_html=True)
            step("4", "執行結果")

            c1, c2, c3 = st.columns(3)
            c1.metric("執行筆數", total_processed)
            c2.metric("成功", total_success)
            c3.metric("失敗", total_fail)

            if total_fail == 0 and total_processed > 0:
                st.success(f"✅ 全部完成，共處理 **{total_processed}** 筆，成功 **{total_success}** 筆。")
            elif total_fail > 0:
                st.warning(f"⚠️ 執行完成，但有 **{total_fail}** 筆失敗，請查看執行過程。")
            else:
                st.info("執行完成，無資料被處理。")


# =========================================================
# 功能：單筆服務訂單
# =========================================================
else:
    single_feature = mode
    step("3", single_feature)

    # -----------------------------------------------------
    # 優惠券建立
    # -----------------------------------------------------
    if single_feature == "優惠券建立":
        info_panel(
            "功能說明",
            [
                "建立單張優惠券，通常用於訂單異動補差額或退款折抵。",
                "建立後請至後台『優惠券管理』確認優惠碼（前綴＋自動英文字母）。",
                "優惠碼建立後不可修改，請確認金額與有效期限後再送出。",
            ],
        )

        step("4", "優惠券資料")
        cv1, cv2 = st.columns(2)
        with cv1:
            cp_title = st.text_input("標題（客人姓名或用途）", key="cp_title")
            cp_prefix = st.text_input("優惠碼前綴", placeholder="例：tpe0707", key="cp_prefix")
            cp_discount = st.number_input("面額（元）", min_value=1, value=1200, step=100, key="cp_discount")
            cp_piece = st.number_input("張數", min_value=1, max_value=10, value=1, key="cp_piece")
        with cv2:
            cp_date_s = st.date_input("有效期限起", value=date.today(), key="cp_date_s")
            cp_date_e = st.date_input("有效期限迄", value=date.today() + timedelta(days=30), key="cp_date_e")
            cp_regions = st.multiselect("適用地區", list(COUPON_COMPANY_ID_MAP.keys()), default=["台北"], key="cp_regions")
            cp_services = st.multiselect("適用服務", list(COUPON_SERVICE_ITEM_MAP.keys()), default=["居家清潔"], key="cp_services")

        cp_type = st.selectbox("優惠券種類", list(COUPON_TYPE_MAP.keys()), index=0, key="cp_type")
        st.markdown(
            '<div class="hint-box">💡 「不得與其他優惠券重複」最常用，適合補差額或一次性折扣。</div>',
            unsafe_allow_html=True,
        )

        if st.button("🎟 建立優惠券", use_container_width=True, key="create_coupon_btn"):
            if not backend_email.strip() or not backend_password.strip():
                st.error("請先輸入後台帳號密碼")
            elif not cp_title.strip():
                st.error("請輸入標題")
            elif not cp_prefix.strip():
                st.error("請輸入優惠碼前綴")
            elif not cp_regions:
                st.error("請選擇適用地區")
            elif not cp_services:
                st.error("請選擇適用服務")
            else:
                try:
                    with st.spinner("建立優惠券中…"):
                        result = create_coupon(
                            env_name=env,
                            backend_email=backend_email.strip(),
                            backend_password=backend_password.strip(),
                            title=cp_title.strip(),
                            discount=cp_discount,
                            date_s=cp_date_s.strftime("%Y-%m-%d"),
                            date_e=cp_date_e.strftime("%Y-%m-%d"),
                            prefix=cp_prefix.strip(),
                            piece=cp_piece,
                            regions=cp_regions,
                            service_items=cp_services,
                            coupon_type=cp_type,
                        )
                    if result["success"]:
                        st.success(
                            f"✅ {result['message']}\n"
                            f"前綴：{result['coupon_prefix']}　面額：{result['discount']}元　張數：{result['piece']}"
                        )
                        st.info("請至後台『優惠券管理』查看完整優惠碼（前綴 + 自動英文字母）")
                    else:
                        st.warning(result["message"])
                except Exception as e:
                    st.error(f"建立失敗：{e}")

    # -----------------------------------------------------
    # LINE 通知產生器（v7.3：多筆、移除區域、加 N-J Memo）
    # -----------------------------------------------------
    if single_feature == "LINE 通知產生器":
        col_left, col_right = st.columns([3, 1])

        with col_left:
            info_panel(
                "使用說明",
                [
                    "輸入已成立訂單編號，每行一個，可一次輸入多筆。",
                    "系統讀取訂單日期、地址、付款方式與金額，區域由地址自動判斷。",
                ],
            )
            line_order_nos_input = st.text_area(
                "訂單編號（每行一個）",
                value="",
                height=120,
                placeholder="LC00211537\nLC00211538",
                key="line_order_nos",
            )
            if st.button("產生 LINE 訊息", use_container_width=True, key="make-line-from-order-no"):
                if not backend_email.strip() or not backend_password.strip():
                    st.error("請先輸入後台帳號密碼")
                else:
                    # 每行 = 一則 LINE 訊息；同行用 , 分隔 = 合併成一則
                    raw_lines = [x.strip() for x in line_order_nos_input.splitlines() if x.strip()]
                    order_groups = []
                    for line in raw_lines:
                        nos = [n.strip() for n in line.split(",") if n.strip()]
                        if nos:
                            order_groups.append(nos)

                    if not order_groups:
                        st.error("請輸入至少一個訂單編號")
                    else:
                        # 先清空舊結果與相關 widget 快取，避免殘留舊值
                        st.session_state.line_from_order_nos_results = []
                        for _k in list(st.session_state.keys()):
                            if _k.startswith("line_text_") or _k.startswith("nj_memo_"):
                                del st.session_state[_k]
                        results_list = []
                        for nos in order_groups:
                            label = "、".join(nos)
                            try:
                                with st.spinner(f"查詢訂單 {label}…"):
                                    line_result, line_text = build_combined_line_message_from_order_nos(
                                        env_name=env,
                                        backend_email=backend_email.strip(),
                                        backend_password=backend_password.strip(),
                                        order_nos=nos,
                                    )
                                # session 物件不存入 session_state（避免序列化問題）
                                safe_result = {k: v for k, v in line_result.items() if k != "session"}
                                results_list.append({
                                    "order_no": label,
                                    "result": safe_result,
                                    "text": line_text,
                                    "error": None,
                                })
                            except Exception as e:
                                results_list.append({
                                    "order_no": label,
                                    "result": None,
                                    "text": "",
                                    "error": str(e),
                                })
                        st.session_state.line_from_order_nos_results = results_list
                        st.rerun()

        with col_right:
            st.markdown('<div class="sec-label">N-J Memo</div>', unsafe_allow_html=True)
            st.text_area(
                "N-J Memo",
                NJ_MEMO,
                height=220,
                key="nj_memo_fixed",
                label_visibility="collapsed",
            )
            copy_button("複製 N-J Memo", NJ_MEMO, "copy-nj-memo-fixed")

        results_list = st.session_state.get("line_from_order_nos_results", [])
        for idx, item in enumerate(results_list):
            if item["error"]:
                st.error(f"訂單 {item['order_no']} 產生失敗：{item['error']}")
                continue

            line_result = item["result"]
            line_text = item["text"]

            all_nos = line_result.get("all_order_nos") or [line_result.get("order_no")]
            order_no_display = "、".join(str(n) for n in all_nos if n)
            is_combined = len(all_nos) > 1
            is_multi_date = line_result.get("multi_date", False)
            if is_combined and is_multi_date:
                combined_note = "　⚠️ 跨日合併單"
            elif is_combined:
                combined_note = "　⚠️ 同日合併單"
            else:
                combined_note = ""
            st.caption(
                f"訂單：{order_no_display}{combined_note}　"
                f"付款方式：{line_result.get('payway')}　"
                f"區域：{line_result.get('region')}　"
                f"金額：{line_result.get('service_amount') or '—'}　"
                f"車馬費：{line_result.get('fare') or '0'}"
            )

            st.text_area(
                f"LINE 訊息（{line_result.get('order_no')}）",
                line_text,
                height=380,
                label_visibility="collapsed",
            )
            copy_button("複製 LINE 訊息", line_text, f"copy-line-msg-{idx}")

            if idx < len(results_list) - 1:
                st.markdown("<hr>", unsafe_allow_html=True)

    # -----------------------------------------------------
    # 舊客快速建單
    # -----------------------------------------------------
    elif single_feature == "舊客快速建單":
        info_panel("功能說明", ["用電話查詢會員與歷史已付款服務。", "多地址客人會顯示各地址近一年紀錄，請先跟客人確認地址。", "可選已知日期查班表，也可依客人需求搜尋可服務日期。"])

        q1, q2 = st.columns(2)
        with q1:
            q_phone = st.text_input("客人電話", key="old_phone")
        with q2:
            q_clean_type = st.selectbox("購買項目", list(CLEAN_TYPE_ID_MAP.keys()), key="old_clean_type")

        if st.button("🔍  查詢會員", use_container_width=True, key="old_lookup_btn"):
            if not backend_email.strip() or not backend_password.strip():
                st.error("請先輸入後台帳號密碼")
                st.stop()
            if not q_phone.strip():
                st.error("請輸入客人電話")
                st.stop()
            try:
                with st.spinner("查詢中…"):
                    st.session_state.q_lookup = quick_lookup_member(
                        env_name=env,
                        backend_email=backend_email.strip(),
                        backend_password=backend_password.strip(),
                        phone=q_phone.strip(),
                        clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type],
                    )
                st.session_state.q_order_result = None
            except Exception as e:
                st.error(f"查詢失敗：{e}")
                st.session_state.q_lookup = None

        lookup = st.session_state.get("q_lookup")
        if lookup is not None:
            member_payload = lookup.get("member_payload")
            st.markdown("<hr>", unsafe_allow_html=True)
            if not member_payload:
                st.warning("查無此會員。請改用上方『新客資料拆解』功能，直接輸入訂購人資料、服務地址、付款方式與載具資料。")
            else:
                member = member_payload.get("member", {})
                addr_list = member_payload.get("member", {}).get("memberAddressList", [])
                addr_options = [a.get("address", "") for a in addr_list if a.get("address")]
                st.markdown(f"**會員姓名：** {member.get('name', '')}　|　**會員電話：** {lookup.get('phone', '')}")

                step("3", "舊客服務資訊")
                info_panel("使用說明", ["先確認服務地址。", "確認服務類別、付款方式與區域。", "依客人狀況選擇『已知日期』或『依需求搜尋』。"])

                if not addr_options:
                    st.error("此會員沒有留存地址，請改用新客建單或先至後台補會員地址。")
                else:
                    last_summary = get_last_paid_summary(lookup["session"], lookup["phone"], member_payload, addr_options)
                    default_addr_index = addr_options.index(last_summary["address"]) if last_summary and last_summary.get("address") in addr_options else 0
                    q_address = st.selectbox("服務地址", addr_options, index=default_addr_index, key="old_address")

                    if len(addr_options) > 1:
                        st.caption(f"⚠️ 此客人留存 {len(addr_options)} 個地址，請務必跟客人確認本次地點是否正確。")
                        per_addr_summary = get_last_paid_per_address(lookup["session"], lookup["phone"], member_payload, addr_options, within_days=365)
                        addr_rows = []
                        for addr in addr_options:
                            info = per_addr_summary.get(addr)
                            if not info:
                                addr_rows.append(f"・{addr}　——　近一年內查無已付款服務紀錄")
                            else:
                                ph_text = f"{info['person']}人{info['hour']}小時" if (info["person"] or info["hour"]) else "未知"
                                payment_text = payment_invoice_display(info.get("payway"), info.get("invoice_text"))
                                addr_rows.append(f"・{addr}　——　{info['date']} {info['time']}　類別：{info['clean_type'] or '未知'}　人時：{ph_text}　{payment_text}")
                        st.markdown('<div class="hint-box">📍 <b>各地址近一年內最近一次已付款服務</b>：<br>' + "<br>".join(addr_rows) + '</div>', unsafe_allow_html=True)

                    default_clean_type = last_summary["clean_type"] if last_summary and last_summary.get("clean_type") in CLEAN_TYPE_ID_MAP else "居家清潔"
                    default_person = int(last_summary["person"]) if last_summary and str(last_summary.get("person", "")).isdigit() else 2
                    q_clean_type_confirm = st.selectbox("服務類別", list(CLEAN_TYPE_ID_MAP.keys()), index=list(CLEAN_TYPE_ID_MAP.keys()).index(default_clean_type), key="old_clean_confirm")
                    q_payway = last_summary.get("payway") if last_summary and last_summary.get("payway") else st.selectbox("付款方式", ["信用卡", "ATM", "儲值金"], key="old_payway")
                    q_region = get_region_by_address(q_address, ACCOUNTS) or "台北"
                    st.caption(f"建單介面：{booking_route_display(q_payway)[0]}　｜　區域：{q_region}")

                    if last_summary:
                        st.markdown(last_summary_card_html(last_summary), unsafe_allow_html=True)

                    upcoming_orders = get_unserved_paid_orders(
                        lookup["session"],
                        lookup["phone"],
                        member_payload,
                        addr_options,
                        today_value=date.today(),
                    )
                    if upcoming_orders:
                        st.markdown(
                            '<div class="hint-box"><b>⚠️ 目前已付款但尚未服務訂單</b><br>'
                            '請先確認客人是否要異動既有訂單，避免重複建單。</div>',
                            unsafe_allow_html=True,
                        )
                        for idx, order in enumerate(upcoming_orders, start=1):
                            ph_text = person_hour_display(order.get("person"), order.get("hour"))
                            payment_text = payment_invoice_display(order.get("payway"), order.get("invoice_text"))
                            address_text = order.get("address") or "未能對應留存地址，請至後台確認"
                            staff_text = order.get("staff") or "待確認"
                            fare_text = f"｜車馬費：{order.get('fare')}" if nonzero_money(order.get("fare")) else ""
                            st.markdown(
                                f"""
                                <div class="history-order">
                                  <div class="history-order-main">{idx}. {h(order.get('order_no'))}　{h(order.get('date'))} {h(order.get('time'), '')}</div>
                                  <div class="history-order-meta">
                                    <div>地址：{h(address_text)}</div>
                                    <div>類別：{h(order.get('clean_type'))}</div>
                                    <div>服務人員：{h(staff_text)}</div>
                                    <div>人時：{h(ph_text)}{h(fare_text, '')}</div>
                                    <div>{h(payment_text)}</div>
                                  </div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                    date_mode = st.radio("日期/班表查詢方式", ["已知日期", "依需求搜尋可服務日期"], horizontal=True, key="old_date_mode")

                    if date_mode == "已知日期":
                        info_panel("已知日期使用說明", ["客人已指定某一天時使用。", "此模式才需要選服務日期與時段。", "若客人只說平日、週末、不限或幾小時，請改選『依需求搜尋可服務日期』。", "人時 = 人數 × 服務時數；09:00-16:00 為 6 小時，09:00-18:00 為 8 小時。"])
                        d1, d2, d3, d4 = st.columns(4)
                        with d1:
                            q_date = st.date_input("服務日期", value=date.today(), key="old_known_date")
                        with d2:
                            q_period = st.selectbox("時段", PERIOD_OPTIONS, key="old_known_period")
                        with d3:
                            q_person = st.number_input("人數", min_value=1, max_value=8, value=default_person, key="old_known_person")
                        with d4:
                            q_hour = PERIOD_HOUR_MAP.get(q_period, 3)
                            st.markdown(f'<br><b>{q_hour} 小時</b>（依時段自動帶出）<br><span style="color:#8E8E93;font-size:13px;">人時：{int(q_person) * int(q_hour)}</span>', unsafe_allow_html=True)

                        if st.button("🔎 查詢該日班表", use_container_width=True, key="old_check_known"):
                            try:
                                with st.spinner("查詢班表中…"):
                                    rows = quick_check_available_slots(
                                        env_name=env,
                                        payway=q_payway,
                                        lookup_result=lookup,
                                        address=q_address,
                                        clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm],
                                        date_s=q_date.strftime("%Y-%m-%d"),
                                        hour=q_hour,
                                        person=q_person,
                                        periods=[q_period],
                                        period_hours=PERIOD_HOUR_MAP,
                                    )
                                st.session_state.old_known_slots = rows
                            except Exception as e:
                                st.session_state.old_known_slots = []
                                st.error(f"查詢班表失敗：{e}")

                        rows = st.session_state.get("old_known_slots")
                        if rows:
                            if any(r.get("available") for r in rows):
                                for r in rows:
                                    st.success(f"{r.get('date')} {r.get('period')} 可安排　服務人員：{r.get('staff') or '待確認'}")
                            else:
                                st.warning("此日期/時段目前無可安排班表。建議改用『依需求搜尋可服務日期』，讓系統依平日/週末/不限條件往後找可服務日期。")

                        if st.button("🚀 建立訂單", use_container_width=True, key="old_create_known"):
                            try:
                                with st.spinner("建單中，請稍候…"):
                                    result = quick_create_order(
                                        env_name=env,
                                        payway=q_payway,
                                        region=q_region,
                                        lookup_result=lookup,
                                        address=q_address,
                                        clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm],
                                        date_s=q_date.strftime("%Y-%m-%d"),
                                        period_s=q_period,
                                        hour=q_hour,
                                        person=q_person,
                                    )
                                    ok, mail_msg = send_confirmation(result)
                                    result["mail_sent"] = ok
                                    result["mail_msg"] = mail_msg
                                st.session_state.q_order_result = result
                            except Exception as e:
                                st.error(f"建單失敗：{e}")

                    else:
                        info_panel("依需求搜尋使用說明", ["客人尚未指定日期時使用，例如只說平日、週末或不限。", "此模式先選日期類型與時段偏好，不需要先選單一日期。", "可選平日 / 週末 / 不限，也可選上午 / 下午 / 不限。", "人時 = 人數 × 服務時數；系統會列出等效方案，例如 2人6小時 = 12人時 = 3人4小時。"])
                        a1, a2, a3, a4 = st.columns(4)
                        with a1:
                            day_type = st.selectbox("日期類型", ["平日", "週末", "不限"], key="old_day_type")
                        with a2:
                            time_pref = st.selectbox("時段偏好", ["上午", "下午", "不限"], key="old_time_pref")
                        with a3:
                            base_person = st.number_input("人數", min_value=1, max_value=8, value=2, key="old_search_person")
                        with a4:
                            base_hour = st.number_input("每人時數", min_value=2, max_value=8, value=4, key="old_search_hour")
                        search_days = st.slider("往後搜尋天數", min_value=7, max_value=60, value=30, step=1, key="old_search_days")
                        plans = build_equivalent_plans(base_person, base_hour)
                        total_ph = int(base_person) * int(base_hour)
                        st.caption(f"人時 = {int(base_person)} 人 × {int(base_hour)} 小時 = {total_ph} 人時")
                        st.caption("將查詢方案：" + "、".join([f"{p['person']}人{p['hour']}小時" for p in plans]))

                        if st.button("🔎 搜尋可服務日期", use_container_width=True, key="old_search_dates"):
                            try:
                                with st.spinner("搜尋可服務日期中…"):
                                    rows = search_available_service_dates(
                                        env_name=env,
                                        payway=q_payway,
                                        lookup_result=lookup,
                                        address=q_address,
                                        clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm],
                                        start_date=date.today(),
                                        days=search_days,
                                        day_type=day_type,
                                        time_preference=time_pref,
                                        plans=plans,
                                        periods=PERIOD_OPTIONS,
                                        period_hours=PERIOD_HOUR_MAP,
                                    )
                                st.session_state.old_search_results = rows
                            except Exception as e:
                                st.session_state.old_search_results = []
                                st.error(f"搜尋失敗：{e}")

                        rows = st.session_state.get("old_search_results")
                        if rows is not None:
                            if rows:
                                st.markdown("**可服務日期搜尋結果**")
                                for idx, r in enumerate(rows[:20]):
                                    st.write(f"{idx+1}. 方案：{r['person']}人{r['hour']}小時　{r['date']} {r['period']}　服務人員：{r.get('staff') or '待確認'}")
                            else:
                                st.warning("目前依條件搜尋不到可服務日期，請放寬日期類型、時段偏好或延長搜尋天數。")

    # -----------------------------------------------------
    # 新客資料拆解
    # -----------------------------------------------------
    elif single_feature == "新客資料拆解":
        info_panel(
            "功能說明",
            [
                "此功能不直接建立訂單，避免與後台新客建單流程重複。",
                "客服貼上客人提供的制式文字後，系統會拆成欄位，方便檢查、修改與複製到後台。",
                "畫面不預設任何個人姓名、電話、Email、地址或載具資料，避免個資外洩。",
            ],
        )

        step("4", "貼上新客制式資料")
        raw_new_customer_text = st.text_area(
            "請貼上客人提供的訂購資料",
            value="",
            height=220,
            placeholder="請貼上完整文字，例如：訂購人姓名、電話、Email、服務地址、坪數、付款方式、發票載具、服務需求等",
            key="new_customer_raw_text",
        )

        parsed_customer = parse_new_customer_order_text(raw_new_customer_text)

        if st.button("拆解資料", use_container_width=True, key="parse_new_customer_text_btn"):
            st.session_state.parsed_new_customer = parsed_customer
            st.session_state.parsed_new_name = parsed_customer.get("name", "")
            st.session_state.parsed_new_phone = parsed_customer.get("phone", "")
            st.session_state.parsed_new_email = parsed_customer.get("email", "")
            st.session_state.parsed_new_address = parsed_customer.get("address", "")
            st.session_state.parsed_new_ping = parsed_customer.get("ping", "")
            st.session_state.parsed_new_payway = parsed_customer.get("payway", "")
            st.session_state.parsed_new_invoice = parsed_customer.get("invoice_type", "")
            st.session_state.parsed_new_invoice_title = parsed_customer.get("invoice_title", "")
            st.session_state.parsed_new_tax_id = parsed_customer.get("tax_id", "")
            st.session_state.parsed_new_carrier = parsed_customer.get("carrier", "")
            st.session_state.parsed_new_requirement = parsed_customer.get("requirement", "")
            st.session_state.parsed_new_note = parsed_customer.get("note", "")
            st.success("已拆解資料，請檢查下方欄位。")

        parsed_customer = st.session_state.get("parsed_new_customer", parsed_customer)

        step("5", "拆解後欄位（可修改）")
        c1, c2, c3 = st.columns(3)
        with c1:
            parsed_name = st.text_input("訂購人姓名", value=parsed_customer.get("name", ""), key="parsed_new_name")
        with c2:
            parsed_phone = st.text_input("訂購人電話", value=parsed_customer.get("phone", ""), key="parsed_new_phone")
        with c3:
            parsed_email = st.text_input("訂購人 Email", value=parsed_customer.get("email", ""), key="parsed_new_email")

        parsed_address = st.text_input("服務地址", value=parsed_customer.get("address", ""), key="parsed_new_address")

        p1, p2, p3, p4 = st.columns(4)
        with p1:
            parsed_ping = st.text_input("室內坪數", value=parsed_customer.get("ping", ""), key="parsed_new_ping")
        with p2:
            parsed_payway = st.text_input("付款方式", value=parsed_customer.get("payway", ""), key="parsed_new_payway")
        with p3:
            parsed_invoice = st.text_input("發票/載具類型", value=parsed_customer.get("invoice_type", ""), key="parsed_new_invoice")
        with p4:
            parsed_carrier = st.text_input("載具號碼", value=parsed_customer.get("carrier", ""), key="parsed_new_carrier")

        i1, i2 = st.columns(2)
        with i1:
            parsed_invoice_title = st.text_input("發票抬頭", value=parsed_customer.get("invoice_title", ""), key="parsed_new_invoice_title")
        with i2:
            parsed_tax_id = st.text_input("統一編號", value=parsed_customer.get("tax_id", ""), key="parsed_new_tax_id")

        parsed_requirement = st.text_input("服務需求", value=parsed_customer.get("requirement", ""), key="parsed_new_requirement")
        parsed_note = st.text_area("其他備註", value=parsed_customer.get("note", ""), height=100, key="parsed_new_note")

        formatted_text = "\\n".join([
            f"訂購人姓名：{parsed_name}",
            f"訂購人電話：{parsed_phone}",
            f"訂購人Email：{parsed_email}",
            f"服務地址：{parsed_address}",
            f"室內坪數：{parsed_ping}",
            f"付款方式：{parsed_payway}",
            f"發票/載具：{parsed_invoice}",
            f"發票抬頭：{parsed_invoice_title}",
            f"統一編號：{parsed_tax_id}",
            f"載具號碼：{parsed_carrier}",
            f"服務需求：{parsed_requirement}",
            f"其他備註：{parsed_note}",
        ])

        st.text_area("整理後文字", formatted_text, height=220, key="parsed_new_formatted_text")

    order_result = st.session_state.get("q_order_result")
    if order_result:
        st.markdown("<hr>", unsafe_allow_html=True)
        step("5", "執行結果")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("訂單編號", order_result["order_no"])
        c2.metric("金額（含稅）", order_result.get("service_amount") or order_result.get("price_with_tax") or "—")
        c3.metric("車馬費", order_result.get("fare") or "0")
        c4.metric("確認信", "已發送" if order_result.get("mail_sent") else "失敗")
        if order_result.get("mail_sent"):
            st.success("✅ 訂單建立成功，確認信已發送。")
        else:
            st.warning(f"確認信發送失敗：{order_result.get('mail_msg', '')}")
        line_message = build_line_message(order_result)
        col_msg, col_memo = st.columns([3, 1])
        with col_msg:
            st.text_area("LINE 訊息內容", line_message, height=420, label_visibility="collapsed")
            copy_button("複製 LINE 訊息", line_message, "copy-line-message")
        with col_memo:
            st.text_area("N-J Memo", NJ_MEMO, height=200, label_visibility="collapsed", key="nj_memo_order_result")
            copy_button("複製 N-J Memo", NJ_MEMO, "copy-nj-memo-order-result")
