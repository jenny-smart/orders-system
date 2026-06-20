# -*- coding: utf-8 -*-
import streamlit as st
from datetime import date

from orders import run_process_web
from quick_order import (
    quick_lookup_member,
    quick_create_order,
    send_confirmation,
    build_line_message,
    get_last_service_summary,
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
    step("2", "查詢客人")

    q1, q2 = st.columns(2)
    with q1:
        q_phone = st.text_input("客人電話")
        q_clean_type = st.selectbox("購買項目", list(CLEAN_TYPE_ID_MAP.keys()))
    with q2:
        q_payway = st.selectbox("付款方式", ["信用卡", "ATM", "儲值金"])
        q_region = st.selectbox("區域（決定 ATM 收款帳戶 / 日曆）", ["台北", "台中", "桃園", "新竹", "高雄"])

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
                q_address = st.selectbox("服務地址", addr_options)

                last_summary = get_last_service_summary(lookup["session"], lookup["phone"], member_payload, q_address)
                default_person = 2
                if last_summary and str(last_summary.get("person", "")).strip().isdigit():
                    default_person = int(last_summary["person"])

                d1, d2, d3, d4 = st.columns(4)
                with d1:
                    q_date = st.date_input("服務日期", value=date.today())
                with d2:
                    q_period = st.selectbox("時段", PERIOD_OPTIONS)
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
                    last_date = last_summary.get("date") or "未知"
                    last_time = last_summary.get("time") or ""
                    last_staff = last_summary.get("staff") or "未知"
                    last_person = last_summary.get("person") or ""
                    last_hour = last_summary.get("hour") or ""
                    person_hour_text = (
                        f"{last_person}人{last_hour}小時" if last_person or last_hour else "未知"
                    )
                    st.markdown(
                        f'<div class="hint-box">'
                        f'📌 <b>上次（已付款）服務</b>：{last_date} {last_time}　|　'
                        f'<b>服務人員</b>：{last_staff}　|　'
                        f'<b>總人時</b>：{person_hour_text}'
                        f'　——　人數已預設帶入上次紀錄，如有變動請手動調整上方「人數」欄位。'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        '<div class="hint-box">📌 查無此地址「已付款」的服務紀錄（可能是新地址，或之前都未完成付款），人數預設為 2 人，請確認後再送出。</div>',
                        unsafe_allow_html=True
                    )

                st.markdown("<hr>", unsafe_allow_html=True)

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
                                clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type],
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

        c1, c2, c3 = st.columns(3)
        c1.metric("訂單編號", order_result["order_no"])
        c2.metric("金額（含稅）", order_result.get("price_with_tax") or "—")
        c3.metric("確認信", "已發送" if order_result.get("mail_sent") else "失敗")

        if not order_result.get("mail_sent"):
            st.warning(f"確認信發送失敗：{order_result.get('mail_msg', '')}")
        else:
            st.success("✅ 訂單建立成功，確認信已發送。")

        st.markdown("**📋 複製貼給客人的 LINE 訊息**")
        st.text_area(
            "LINE 訊息內容",
            build_line_message(order_result),
            height=420,
            label_visibility="collapsed",
        )
