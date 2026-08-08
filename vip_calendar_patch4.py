# -*- coding: utf-8 -*-
"""Fourth-stage VIP UI patch.

Goals:
- Never auto-decide which Google Calendar event corresponds to a backend order.
- Keep the upper query result compact.
- Show backend orders on the left and Google Calendar events on the right.
- For calendar edits, the user explicitly selects the calendar event.
"""


def _color_meta(vcs, row):
    cid = str(row.get("color_id") or "")
    if cid == str(vcs.COLOR_PURPLE):
        return "🟣", "未安排"
    if cid == str(vcs.COLOR_YELLOW):
        return "🟡", "已安排"
    if cid == str(vcs.COLOR_GREEN):
        return "🟢", "暫停"
    return "⚪", "未指定"


def _compact_results(vcs, customer):
    st = vcs.st
    st.markdown("## 查詢結果")
    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("### 後台")
        orders = sorted(customer.get("orders") or [], key=lambda x: (str(x.get("date") or ""), str(x.get("time") or "")))
        if not orders:
            st.caption("此範圍沒有已付款訂單")
        for row in orders:
            st.write(
                f"**{row.get('date','')} {str(row.get('time') or '').replace(' ','')}**｜"
                f"{row.get('order_no','')}｜{row.get('payway','')}"
            )

    with right:
        st.markdown("### Google 日曆")
        rows = sorted(customer.get("calendar_events") or [], key=lambda x: (str(x.get("date") or ""), str(x.get("period") or "")))
        if customer.get("calendar_lookup_error"):
            st.warning(customer.get("calendar_lookup_error"))
        elif not rows:
            st.caption("此範圍沒有符合手機號碼的日曆事件")
        for row in rows:
            icon, status = _color_meta(vcs, row)
            st.write(
                f"**{row.get('date','')} {row.get('period','')}**｜{icon}{status}｜{row.get('summary','')}"
            )


def _event_label(vcs, row):
    icon, status = _color_meta(vcs, row)
    return f"{row.get('date','')} {row.get('period','')}｜{icon}{status}｜{row.get('summary','')}"


def _render_manual_ui(vcs, vcp, backend_email, backend_password, env_name):
    st = vcs.st
    st.markdown("### VIP 訂單／Google 日曆同步")
    st.caption("先查詢，再由你自行選擇要操作的後台訂單或 Google 日曆事件。系統不自動配對。")

    phone = st.text_input("VIP 客戶手機號碼", key="vipcal_phone", placeholder="09xxxxxxxx")
    if st.button("🔎 查詢後台＋Google 日曆", key="vipcal_lookup", type="primary", use_container_width=True):
        try:
            data = vcs.load_vip_customer(env_name, backend_email, backend_password, phone)
            data["lookup"]["backend_email"] = backend_email
            data["lookup"]["backend_password"] = backend_password
            st.session_state.vipcal_customer = data
        except Exception as exc:
            st.session_state.vipcal_customer = None
            st.error(str(exc))

    customer = st.session_state.get("vipcal_customer")
    if not customer:
        return

    _compact_results(vcs, customer)
    st.divider()
    action = st.radio(
        "處理方式",
        ["異動日期／時段", "取消／暫停", "僅新增日曆", "先預約再新增日曆", "修改日曆資訊"],
        horizontal=True,
        key="vipcal_action",
    )

    orders_list = customer.get("orders") or []
    calendar_rows = customer.get("calendar_events") or []

    if action == "修改日曆資訊":
        if not calendar_rows:
            st.warning("沒有可修改的 Google 日曆事件")
            return
        labels = [_event_label(vcs, r) for r in calendar_rows]
        chosen = st.selectbox("選擇要修改的 Google 日曆事件", labels, key="vipcal_manual_event")
        row = calendar_rows[labels.index(chosen)]
        event = row.get("event") or {}
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        start_dt = vcs.orders.parse_event_time(start) if start else None
        end_dt = vcs.orders.parse_event_time(end) if end else None
        if not start_dt or not end_dt:
            st.error("此事件不是標準日期時間格式")
            return
        start_dt = start_dt.astimezone(vcs.TAIPEI_TZ)
        end_dt = end_dt.astimezone(vcs.TAIPEI_TZ)
        current_period = f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"
        periods = list(vcs.STANDARD_PERIODS)
        if current_period not in periods:
            periods.insert(0, current_period)
        c1, c2 = st.columns(2)
        with c1:
            new_date = st.date_input("日曆日期", value=start_dt.date(), key="vipcal_manual_date")
        with c2:
            new_period = st.selectbox("日曆時段", periods, index=periods.index(current_period), key="vipcal_manual_period")
        c3, c4 = st.columns(2)
        with c3:
            confirm = st.selectbox("確認文字", ["保持不變", "每月確認", "已確認"], key="vipcal_manual_confirm")
        with c4:
            color = st.selectbox("安排狀態", ["保持不變", "紫色／未安排", "黃色／已安排", "綠色／暫停"], key="vipcal_manual_color")
        if st.button("✅ 修改 Google 日曆", key="vipcal_manual_save", type="primary", use_container_width=True):
            from vip_calendar_patch3 import _update_calendar_schedule
            try:
                _update_calendar_schedule(vcs, vcp, row, new_date, new_period, confirm, color)
                st.success("✅ Google 日曆已更新")
                refreshed = vcs.load_vip_customer(env_name, backend_email, backend_password, customer.get("phone", ""))
                refreshed["lookup"]["backend_email"] = backend_email
                refreshed["lookup"]["backend_password"] = backend_password
                st.session_state.vipcal_customer = refreshed
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        return

    if not orders_list:
        st.warning("此範圍沒有後台已付款訂單可作為操作來源")
        return
    order_labels = [vcs._order_label(o) for o in orders_list]
    selected_order_label = st.selectbox("選擇後台訂單／範本", order_labels, key="vipcal_manual_order")
    source = orders_list[order_labels.index(selected_order_label)]

    if action == "異動日期／時段":
        c1, c2 = st.columns(2)
        with c1:
            from datetime import datetime
            default_date = datetime.strptime(str(source.get("date")), "%Y-%m-%d").date()
            new_date = st.date_input("新服務日期", value=default_date, key="vipcal_change_date_manual")
        with c2:
            current_period = str(source.get("time") or "").replace(" ", "")
            periods = list(vcs.STANDARD_PERIODS)
            if current_period not in periods:
                periods.insert(0, current_period)
            new_period = st.selectbox("新服務時段", periods, index=periods.index(current_period), key="vipcal_change_period_manual")
        if st.button("🔎 先確認後台可異動", key="vipcal_manual_change_check", use_container_width=True):
            try:
                check = vcs.check_backend_change_slot(customer, source, new_date.isoformat(), new_period)
                st.session_state.vipcal_manual_change_result = check
            except Exception as exc:
                st.error(str(exc))
        check = st.session_state.get("vipcal_manual_change_result")
        if check and check.get("available"):
            st.success(f"可異動：{check.get('staff') or '有可用時段'}")
            st.info("後台異動成功後，再由你自行選擇要修改的 Google 日曆事件。系統不自動配對。")
        return

    if action == "取消／暫停":
        st.info("取消會先處理後台；Google 日曆請再到『修改日曆資訊』自行選擇事件並改成綠色／暫停。")
        return

    if action == "僅新增日曆":
        st.info("此功能只新增日曆，不會自動對應既有事件。")
        return

    if action == "先預約再新增日曆":
        st.info("先建立後台訂單；日曆部分不自動挑選既有事件，後續由你自行選擇修改或新增。")
        return


def apply_patch(vcs, vcp):
    vcs.render_vip_calendar_sync = lambda backend_email, backend_password, env_name: _render_manual_ui(
        vcs, vcp, backend_email, backend_password, env_name
    )
    return vcs
