# -*- coding: utf-8 -*-
import html
import json
import streamlit as st
import streamlit.components.v1 as components
from datetime import date

from orders import run_process_web, get_region_by_address
from accounts import ACCOUNTS
from quick_order import (
    quick_lookup_member,
    quick_create_order,
    quick_check_available_slots,
    send_confirmation,
    build_line_message,
    build_line_message_from_order_no,
    get_last_paid_summary,
    get_last_paid_per_address,
    get_unserved_paid_orders,
    get_last_purchase_fetch_debug,
)

st.set_page_config(page_title="儲值金訂單系統", page_icon="💰", layout="wide")

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

/* 執行過程拉高 + 保留換行 */
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
        ">複製 LINE 訊息</button>
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
  <div class="hero-emoji">💰</div>
  <div>
    <div class="hero-title">儲值金訂單系統</div>
    <div class="hero-sub">支援批次建單（Google Sheet）與單筆快速建單，可寄確認信、改 Google 日曆。</div>
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

mode = st.radio(
    "操作模式",
    ["批次建單（Google Sheet）", "單筆快速建單"],
    horizontal=True,
)

st.markdown("<hr>", unsafe_allow_html=True)


# =========================================================
# 模式一：批次建單（Google Sheet）
# =========================================================
if mode == "批次建單（Google Sheet）":

    step("2", "執行設定")

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
# 模式二：單筆快速建單
# =========================================================
else:
    with st.expander("📋 依訂單編號產生 LINE 通知訊息"):
        o1, o2, o3 = st.columns([2, 1, 1])
        with o1:
            line_order_no = st.text_input("訂單編號", placeholder="例：LC00211517", key="line_order_no")
        with o2:
            line_fallback_payway = st.selectbox("付款方式（抓不到時使用）", ["信用卡", "ATM", "儲值金"], key="line_fallback_payway")
        with o3:
            line_fallback_region = st.selectbox("區域（抓不到時使用）", ["台北", "台中", "桃園", "新竹", "高雄"], key="line_fallback_region")

        make_line_clicked = st.button("產生 LINE 訊息", use_container_width=True, key="make-line-from-order-no")
        if make_line_clicked:
            if not backend_email.strip() or not backend_password.strip():
                st.error("請先輸入後台帳號密碼")
            elif not line_order_no.strip():
                st.error("請輸入訂單編號")
            else:
                try:
                    with st.spinner("查詢訂單並產生訊息中…"):
                        line_result, line_text = build_line_message_from_order_no(
                            env_name=env,
                            backend_email=backend_email.strip(),
                            backend_password=backend_password.strip(),
                            order_no=line_order_no.strip(),
                            fallback_payway=line_fallback_payway,
                            fallback_region=line_fallback_region,
                        )
                    st.session_state.line_from_order_no_result = line_result
                    st.session_state.line_from_order_no_text = line_text
                except Exception as e:
                    st.session_state.line_from_order_no_result = None
                    st.session_state.line_from_order_no_text = ""
                    st.error(f"產生失敗：{e}")

        line_text = st.session_state.get("line_from_order_no_text", "")
        line_result = st.session_state.get("line_from_order_no_result")
        if line_text and line_result:
            st.caption(
                f"訂單：{line_result.get('order_no')}　付款方式：{line_result.get('payway')}　"
                f"區域：{line_result.get('region')}　金額：{line_result.get('service_amount') or '—'}　"
                f"車馬費：{line_result.get('fare') or '0'}"
            )
            st.text_area("訂單 LINE 訊息內容", line_text, height=420, label_visibility="collapsed")
            copy_button("複製 LINE 訊息", line_text, "copy-line-message-from-order-no")

    st.markdown("<hr>", unsafe_allow_html=True)

    step("2", "查詢客人")

    q1, q2 = st.columns(2)
    with q1:
        q_phone = st.text_input("客人電話")
    with q2:
        q_clean_type = st.selectbox("購買項目", list(CLEAN_TYPE_ID_MAP.keys()))

    lookup_clicked = st.button("🔍  查詢會員", use_container_width=True)

    if lookup_clicked:
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
            st.warning("查無此會員（新客人），請先傳下方話術跟客人收集資料，後台手動建會員後再回來查詢一次：")
            st.code(
                "您好，請您提供如下訂購人資訊，以利協助您預約，謝謝\n"
                "訂購人姓名：\n訂購人電話：\n訂購人Email：\n服務地址：\n室內坪數 :\n"
                "付款方式： 信用卡 / 轉帳匯款 擇一  \n"
                "發票載具：會員載具/手機載具(請提供載碼 ) 或 統編發票( 請留下 公司抬頭及統一編號)",
                language=None,
            )
        else:
            member = member_payload.get("member", {})
            addr_list = member_payload.get("member", {}).get("memberAddressList", [])
            addr_options = [a.get("address", "") for a in addr_list if a.get("address")]

            st.markdown(f"**會員姓名：** {member.get('name', '')}　|　**會員電話：** {lookup.get('phone', '')}")

            step("3", "服務資訊（同上次客人 - 除非有多個地址才需要改）")

            if not addr_options:
                st.error("此會員沒有留存地址，請改用後台手動建單，或先請客人提供完整地址。")
            else:
                # 「曾服務過」一律只看「電話 + 已付款」，不分地址、不分服務類別，
                # 找到的這筆同時拿來預設地址/服務類別/人數/時數/付款方式/發票。
                last_summary = get_last_paid_summary(lookup["session"], lookup["phone"], member_payload, addr_options)

                default_addr_index = 0
                if last_summary and last_summary.get("address") in addr_options:
                    default_addr_index = addr_options.index(last_summary["address"])

                q_address = st.selectbox("服務地址", addr_options, index=default_addr_index)

                if len(addr_options) > 1:
                    st.caption(
                        f"⚠️ 此客人留存 {len(addr_options)} 個地址，已預設選擇上次「已付款」服務的地址，"
                        f"請務必跟客人確認本次地點是否正確。"
                    )

                    per_addr_summary = get_last_paid_per_address(
                        lookup["session"], lookup["phone"], member_payload, addr_options, within_days=365
                    )
                    addr_rows = []
                    for addr in addr_options:
                        info = per_addr_summary.get(addr)
                        if not info:
                            addr_rows.append(f"・{addr}　——　近一年內查無已付款服務紀錄")
                        else:
                            ph_text = f"{info['person']}人{info['hour']}小時" if (info["person"] or info["hour"]) else "未知"
                            payment_text = payment_invoice_display(info.get("payway"), info.get("invoice_text"))
                            fare_text = f"　車馬費：{info['fare']}" if nonzero_money(info.get("fare")) else ""
                            addr_rows.append(
                                f"・{addr}　——　{info['date']} {info['time']}　"
                                f"類別：{info['clean_type'] or '未知'}　服務人員：{info['staff'] or '未知'}　"
                                f"總人時：{ph_text}　{payment_text}{fare_text}"
                            )
                    st.markdown(
                        f'<div class="hint-box">'
                        f'📍 <b>各地址近一年內最近一次已付款服務</b>：<br>'
                        + "<br>".join(addr_rows) +
                        f'</div>',
                        unsafe_allow_html=True
                    )

                default_person = 2
                if last_summary and str(last_summary.get("person", "")).strip().isdigit():
                    default_person = int(last_summary["person"])

                default_clean_type = "居家清潔"
                if last_summary and last_summary.get("clean_type") in CLEAN_TYPE_ID_MAP:
                    default_clean_type = last_summary["clean_type"]
                clean_type_index = list(CLEAN_TYPE_ID_MAP.keys()).index(default_clean_type)

                default_period = compact_period(last_summary.get("time")) if last_summary else ""
                period_index = PERIOD_OPTIONS.index(default_period) if default_period in PERIOD_OPTIONS else 0

                e1, e2 = st.columns(2)
                with e1:
                    q_clean_type_confirm = st.selectbox(
                        "服務類別（預設同上次，可調整）",
                        list(CLEAN_TYPE_ID_MAP.keys()),
                        index=clean_type_index,
                        key="q_clean_type_confirm",
                    )
                with e2:
                    if last_summary and q_clean_type_confirm != q_clean_type:
                        st.caption(f"⚠️ 與查詢會員時所選的「{q_clean_type}」不同，將以這裡選的「{q_clean_type_confirm}」為準建單。")

                d1, d2, d3, d4 = st.columns(4)
                with d1:
                    q_date = st.date_input("服務日期", value=date.today())
                with d2:
                    q_period = st.selectbox("時段", PERIOD_OPTIONS, index=period_index)
                with d3:
                    q_person = st.number_input(
                        "人數",
                        min_value=1,
                        max_value=8,
                        value=default_person,
                        help="預設帶入上次服務人數，可手動調整",
                    )
                with d4:
                    q_hour = PERIOD_HOUR_MAP.get(q_period, 3)
                    st.markdown(f"<br><b>{q_hour} 小時</b>（依時段自動帶出）", unsafe_allow_html=True)

                if last_summary:
                    st.markdown(last_summary_card_html(last_summary), unsafe_allow_html=True)
                else:
                    st.markdown(
                        '<div class="hint-box">📌 查無此客人任何「已付款」紀錄（可能是新客人，或之前都未完成付款），各欄位請手動確認後再送出。</div>',
                        unsafe_allow_html=True
                    )
                    debug_info = get_last_purchase_fetch_debug()
                    with st.expander("🔧 除錯資訊（查無紀錄時可展開確認是否為請求問題）"):
                        st.write(f"實際請求網址：{debug_info.get('request_url', '')}")
                        st.write(f"最終回應網址：{debug_info.get('final_url', '')}")
                        st.write(f"回應狀態碼：{debug_info.get('status_code', '')}")
                        st.write(f"頁面抓到的訂單區塊數（篩選前）：{debug_info.get('raw_block_count', '')}")
                        st.write(f"篩選電話後剩下的區塊數：{debug_info.get('filtered_block_count', '')}")
                        st.write(f"付款狀態篩選：{debug_info.get('purchase_status_filter', '') or '全部'}")
                        if debug_info.get("looks_like_login_page"):
                            st.error("⚠️ 回應內容疑似是登入頁，而不是訂單列表頁，可能是 session 過期或被導回登入。")
                        st.code(debug_info.get("snippet", ""), language=None)

                # 付款方式：優先用上次「已付款」紀錄自動帶出，不需要每次手動選。
                # 只有完全查無上次付款紀錄時，才退而求其次給一個小選單讓客服指定。
                if last_summary and last_summary.get("payway"):
                    q_payway = last_summary["payway"]
                else:
                    q_payway = st.selectbox(
                        "付款方式（查無上次紀錄，請手動指定）",
                        ["信用卡", "ATM", "儲值金"],
                    )
                route_customer_type, route_path = booking_route_display(q_payway)
                st.caption(f"建單介面：{route_customer_type}　{route_path}")

                # 區域：直接用服務地址自動判斷（決定 ATM 收款帳戶 / 日曆），不需要手動選。
                q_region = get_region_by_address(q_address, ACCOUNTS) or "台北"
                st.caption(f"區域（自動判斷，決定 ATM 收款帳戶/日曆）：{q_region}")

                unserved_orders = get_unserved_paid_orders(lookup["session"], lookup["phone"], member_payload, addr_options)
                if unserved_orders:
                    rows_html = []
                    for o in unserved_orders:
                        ph_text = f"{o['person']}人{o['hour']}小時" if (o["person"] or o["hour"]) else "未知"
                        pay_invoice = "" if o["payway"] == "儲值金" else f"　付款：{o['payway'] or '未知'}　發票：{o['invoice_text'] or '未知'}"
                        fare_text = f"　車馬費：{o['fare']}" if nonzero_money(o.get("fare")) else ""
                        rows_html.append(
                            f"・{o['order_no']}　{o['date']} {o['time']}　地址：{o['address'] or '未知'}　"
                            f"類別：{o['clean_type'] or '未知'}　人時：{ph_text}{pay_invoice}{fare_text}"
                        )
                    st.markdown(
                        f'<div class="hint-box" style="border-left-color:#FF3B30;background:#FFF5F5;">'
                        f'⚠️ <b>此客人目前還有 {len(unserved_orders)} 筆「已付款但服務日期還沒到」的訂單</b>，'
                        f'請先確認這次是要新增一筆服務，還是其實是要異動下面這些既有訂單（異動請改用後台『修改日期』，不要在這裡重新建單）：<br>'
                        + "<br>".join(rows_html) +
                        f'</div>',
                        unsafe_allow_html=True
                    )

                st.markdown("<hr>", unsafe_allow_html=True)

                s1, s2 = st.columns([1, 2])
                with s1:
                    check_slots_clicked = st.button("🔎 查詢班表可排時段", use_container_width=True)
                with s2:
                    st.caption("會用目前地址、服務類別、人數、時數向後台查班表；只查詢，不會建立訂單。")

                if check_slots_clicked:
                    try:
                        with st.spinner("查詢班表中…"):
                            st.session_state.q_available_slots = quick_check_available_slots(
                                env_name=env,
                                payway=q_payway,
                                lookup_result=lookup,
                                address=q_address,
                                clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm],
                                date_s=q_date.strftime("%Y-%m-%d"),
                                hour=q_hour,
                                person=q_person,
                                periods=PERIOD_OPTIONS,
                                period_hours=PERIOD_HOUR_MAP,
                            )
                    except Exception as e:
                        st.session_state.q_available_slots = []
                        st.error(f"查詢班表失敗：{e}")

                available_slots = st.session_state.get("q_available_slots")
                if available_slots:
                    st.markdown("**可排時段查詢結果**")
                    cols = st.columns(2)
                    for idx, row in enumerate(available_slots):
                        status = "✅ 有班表" if row.get("available") else "— 無班表"
                        staff = f"　服務人員：{row.get('staff')}" if row.get("staff") else ""
                        with cols[idx % 2]:
                            st.write(f"{row.get('period')}　{status}{staff}")

                create_clicked = st.button("🚀  建立訂單", use_container_width=True)

                if create_clicked:
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
                        st.session_state.q_order_result = None

    order_result = st.session_state.get("q_order_result")

    if order_result:
        st.markdown("<hr>", unsafe_allow_html=True)
        step("4", "執行結果")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("訂單編號", order_result["order_no"])
        if order_result.get("payway") == "儲值金":
            c2.metric("本次扣儲值金（含稅）", order_result.get("service_amount") or order_result.get("price_with_tax") or "—")
        else:
            c2.metric("金額（含稅）", order_result.get("service_amount") or order_result.get("price_with_tax") or "—")
        c3.metric("車馬費", order_result.get("fare") or "0")
        c4.metric("確認信", "已發送" if order_result.get("mail_sent") else "失敗")

        if not order_result.get("mail_sent"):
            st.warning(f"確認信發送失敗：{order_result.get('mail_msg', '')}")
        else:
            st.success("✅ 訂單建立成功，確認信已發送。")

        st.markdown("**📋 複製貼給客人的 LINE 訊息**")
        line_message = build_line_message(order_result)
        st.text_area(
            "LINE 訊息內容",
            line_message,
            height=420,
            label_visibility="collapsed",
        )
        copy_button("複製 LINE 訊息", line_message, "copy-line-message")
