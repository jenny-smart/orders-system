# -*- coding: utf-8 -*-
from datetime import date, timedelta
import calendar

import streamlit as st
import vip_calendar_sync as vcs
from vip_calendar_patch import apply_patch

# Test the newer VIP calendar behavior without touching ordersapp.py yet.
apply_patch(vcs)

st.set_page_config(page_title="VIP 訂單／Google 日曆同步測試", layout="wide")
st.title("VIP 訂單／Google 日曆同步測試")

col1, col2, col3 = st.columns([3.2, 3.2, 1.2])
with col1:
    backend_email = st.text_input("後台帳號")
with col2:
    backend_password = st.text_input("後台密碼", type="password")
with col3:
    env_label = st.selectbox("環境", ["prod（正式機 backend）", "dev（測試機 backend-dev）"], index=1)
    env = "dev" if env_label.startswith("dev") else "prod"

st.divider()

# Query scope: keep backend + Google Calendar lookups within a deliberate range.
query_mode = st.radio("查詢方式", ["月份", "日期區間"], horizontal=True, key="vipcal_query_mode")
today = date.today()

if query_mode == "月份":
    year_options = list(range(today.year - 1, today.year + 3))
    q1, q2 = st.columns(2)
    with q1:
        query_year = st.selectbox("年份", year_options, index=year_options.index(today.year), key="vipcal_query_year")
    with q2:
        query_month = st.selectbox("月份", list(range(1, 13)), index=today.month - 1, format_func=lambda m: f"{m} 月", key="vipcal_query_month")
    last_day = calendar.monthrange(int(query_year), int(query_month))[1]
    query_date_s = date(int(query_year), int(query_month), 1)
    query_date_e = date(int(query_year), int(query_month), last_day)
    st.caption(f"將同步查詢後台訂單與 Google 日曆：{query_date_s.isoformat()} ～ {query_date_e.isoformat()}")
else:
    r1, r2 = st.columns(2)
    with r1:
        query_date_s = st.date_input("查詢起日", value=today - timedelta(days=30), key="vipcal_range_s")
    with r2:
        query_date_e = st.date_input("查詢迄日", value=today + timedelta(days=90), key="vipcal_range_e")
    if query_date_s > query_date_e:
        st.error("查詢起日不可晚於查詢迄日")
        st.stop()

# The patch reads these normalized values when the user clicks 查詢 VIP 訂單.
st.session_state["vipcal_query_date_s"] = query_date_s.isoformat()
st.session_state["vipcal_query_date_e"] = query_date_e.isoformat()

vcs.render_vip_calendar_sync(backend_email.strip(), backend_password.strip(), env)

# Show the synchronized calendar rows returned by the same lookup.
customer = st.session_state.get("vipcal_customer")
if customer:
    st.divider()
    st.markdown("#### 同步查詢結果")
    st.caption(
        f"查詢範圍：{customer.get('query_date_s', query_date_s.isoformat())} ～ "
        f"{customer.get('query_date_e', query_date_e.isoformat())}"
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**後台已付款訂單**")
        backend_rows = customer.get("orders") or []
        if backend_rows:
            for row in backend_rows:
                st.write(
                    f"{row.get('order_no', '')}｜{row.get('date', '')} {row.get('time', '')}｜"
                    f"{row.get('address', '')}｜{row.get('payway', '')}"
                )
        else:
            st.info("此範圍沒有後台已付款訂單")

    with right:
        st.markdown("**Google 日曆**")
        if customer.get("calendar_lookup_error"):
            st.warning(f"日曆查詢失敗：{customer.get('calendar_lookup_error')}")
        calendar_rows = customer.get("calendar_events") or []
        if calendar_rows:
            color_names = {
                str(vcs.COLOR_PURPLE): "紫色／預排",
                str(vcs.COLOR_YELLOW): "黃色／已確認",
                "10": "綠色／取消暫停",
            }
            for row in calendar_rows:
                color_text = color_names.get(str(row.get("color_id") or ""), f"顏色 {row.get('color_id') or '預設'}")
                st.write(
                    f"{row.get('date', '')} {row.get('period', '')}｜{color_text}｜{row.get('summary', '')}"
                )
        elif not customer.get("calendar_lookup_error"):
            st.info("此範圍沒有符合手機號碼的 Google 日曆事件")
