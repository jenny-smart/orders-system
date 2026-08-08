# -*- coding: utf-8 -*-
"""VIP manual-sync UI.

Rules:
- Never auto-decide which existing Google Calendar event corresponds to an order.
- Every backend operation that needs calendar sync includes an explicit calendar selector.
- Purple = 未安排, Yellow = 已安排, Green = 暫停.
"""

import re
from datetime import datetime


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
        rows = sorted(customer.get("orders") or [], key=lambda x: (str(x.get("date") or ""), str(x.get("time") or "")))
        if not rows:
            st.caption("此範圍沒有已付款訂單")
        for row in rows:
            st.write(f"**{row.get('date','')} {str(row.get('time') or '').replace(' ','')}**｜{row.get('order_no','')}｜{row.get('payway','')}")
    with right:
        st.markdown("### Google 日曆")
        rows = sorted(customer.get("calendar_events") or [], key=lambda x: (str(x.get("date") or ""), str(x.get("period") or "")))
        if customer.get("calendar_lookup_error"):
            st.warning(customer.get("calendar_lookup_error"))
        elif not rows:
            st.caption("此範圍沒有符合手機號碼的日曆事件")
        for row in rows:
            icon, status = _color_meta(vcs, row)
            st.write(f"**{row.get('date','')} {row.get('period','')}**｜{icon}{status}｜{row.get('summary','')}")


def _event_label(vcs, row):
    icon, status = _color_meta(vcs, row)
    return f"{row.get('date','')} {row.get('period','')}｜{icon}{status}｜{row.get('summary','')}"


def _choose_calendar(st, vcs, rows, key, label="選擇要同步的 Google 日曆事件"):
    if not rows:
        st.warning("目前查詢範圍沒有 Google 日曆事件")
        return None
    labels = [_event_label(vcs, r) for r in rows]
    chosen = st.selectbox(label, labels, key=key)
    return rows[labels.index(chosen)]


def _period_input(st, vcs, source, prefix, date_label="服務日期", period_label="服務時段"):
    source_date = datetime.strptime(str(source.get("date")), "%Y-%m-%d").date()
    current_period = str(source.get("time") or "").replace(" ", "")
    periods = list(vcs.STANDARD_PERIODS)
    if current_period not in periods:
        periods.insert(0, current_period)
    c1, c2 = st.columns(2)
    with c1:
        new_date = st.date_input(date_label, value=source_date, key=f"{prefix}_date")
    with c2:
        new_period = st.selectbox(period_label, periods, index=periods.index(current_period), key=f"{prefix}_period")
    return new_date, new_period


def _patch_calendar(vcs, vcp, row, new_date, new_period, confirmation, color):
    from vip_calendar_patch3 import _update_calendar_schedule
    return _update_calendar_schedule(vcs, vcp, row, new_date, new_period, confirmation, color)


def _create_calendar_direct(vcs, customer, source, date_s, period_s, arranged, order_no=""):
    region = vcs._region_from_order(source)
    service = vcs.build_calendar_service()
    calendar_id = vcs._calendar_id(region)
    start_dt, end_dt = vcs._event_range(date_s, period_s)
    name = customer.get("name") or "VIP"
    phone = customer.get("phone") or ""
    prefix = "<已確認/自行預約>" if arranged else "<每月確認/自行預約>"
    body = {
        "summary": f"{prefix}{name},{phone}",
        "location": source.get("address", ""),
        "description": (f"訂單編號：{order_no}" if order_no else "VIP預排／尚未成單"),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Taipei"},
        "colorId": str(vcs.COLOR_YELLOW if arranged else vcs.COLOR_PURPLE),
    }
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def _refresh(vcs, st, env_name, email, password, customer):
    refreshed = vcs.load_vip_customer(env_name, email, password, customer.get("phone", ""))
    refreshed["lookup"]["backend_email"] = email
    refreshed["lookup"]["backend_password"] = password
    st.session_state.vipcal_customer = refreshed


def _render_manual_ui(vcs, vcp, backend_email, backend_password, env_name):
    st = vcs.st
    st.markdown("### VIP 訂單／Google 日曆同步")
    st.caption("系統不自動配對日曆；需要修改既有日曆時，由你自行選擇事件。")

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
    action = st.radio("處理方式", ["異動日期／時段", "取消／暫停", "僅新增日曆", "先預約再新增日曆", "修改日曆資訊"], horizontal=True, key="vipcal_action")
    orders_list = customer.get("orders") or []
    calendar_rows = customer.get("calendar_events") or []

    if action == "修改日曆資訊":
        row = _choose_calendar(st, vcs, calendar_rows, "vipcal_edit_pick", "選擇要修改的 Google 日曆事件")
        if not row:
            return
        event = row.get("event") or {}
        start = vcs.orders.parse_event_time((event.get("start") or {}).get("dateTime"))
        end = vcs.orders.parse_event_time((event.get("end") or {}).get("dateTime"))
        if not start or not end:
            st.error("此事件不是標準日期時間格式")
            return
        start = start.astimezone(vcs.TAIPEI_TZ); end = end.astimezone(vcs.TAIPEI_TZ)
        current_period = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
        periods = list(vcs.STANDARD_PERIODS)
        if current_period not in periods:
            periods.insert(0, current_period)
        c1, c2 = st.columns(2)
        with c1: new_date = st.date_input("日曆日期", value=start.date(), key="vipcal_edit_date")
        with c2: new_period = st.selectbox("日曆時段", periods, index=periods.index(current_period), key="vipcal_edit_period")
        c3, c4 = st.columns(2)
        with c3: confirm = st.selectbox("確認文字", ["保持不變", "每月確認", "已確認"], key="vipcal_edit_confirm")
        with c4: color = st.selectbox("安排狀態", ["保持不變", "紫色／未安排", "黃色／已安排", "綠色／暫停"], key="vipcal_edit_color")
        if st.button("✅ 修改 Google 日曆", key="vipcal_edit_save", type="primary", use_container_width=True):
            try:
                _patch_calendar(vcs, vcp, row, new_date, new_period, confirm, color)
                _refresh(vcs, st, env_name, backend_email, backend_password, customer)
                st.success("✅ Google 日曆已更新")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        return

    if not orders_list:
        st.warning("此範圍沒有後台訂單可作為來源")
        return
    order_labels = [vcs._order_label(o) for o in orders_list]
    selected_order_label = st.selectbox("選擇後台訂單／範本", order_labels, key="vipcal_order")
    source = orders_list[order_labels.index(selected_order_label)]

    if action == "異動日期／時段":
        new_date, new_period = _period_input(st, vcs, source, "vipcal_change", "新服務日期", "新服務時段")
        cal_row = _choose_calendar(st, vcs, calendar_rows, "vipcal_change_cal", "選擇異動成功後要一起移動的 Google 日曆事件")
        if st.button("🔎 先確認後台可異動", key="vipcal_change_check", use_container_width=True):
            try:
                st.session_state.vipcal_change_ok = vcs.check_backend_change_slot(customer, source, new_date.isoformat(), new_period)
            except Exception as exc:
                st.error(str(exc))
        check = st.session_state.get("vipcal_change_ok")
        if check and check.get("available"):
            st.success(f"可異動：{check.get('staff') or '有可用時段'}")
            if cal_row and st.button("✅ 異動後台＋同步選取日曆", key="vipcal_change_exec", type="primary", use_container_width=True):
                result = vcs.change_backend_order_date(customer, source, new_date.isoformat(), new_period)
                if not result.get("ok"):
                    st.error(result.get("message", "後台異動失敗")); return
                try:
                    _patch_calendar(vcs, vcp, cal_row, new_date, new_period, "已確認", "黃色／已安排")
                    _refresh(vcs, st, env_name, backend_email, backend_password, customer)
                    st.success("✅ 後台與選取的 Google 日曆已同步異動")
                    st.rerun()
                except Exception as exc:
                    st.error(f"⚠️ 後台已異動，但日曆同步失敗：{exc}")
        return

    if action == "取消／暫停":
        cal_row = _choose_calendar(st, vcs, calendar_rows, "vipcal_cancel_cal", "選擇取消後要改為綠色／暫停的 Google 日曆事件")
        cancel_status = st.radio("取消處理方式", ["不需退款", "待退款", "待收異動"], horizontal=True, key="vipcal_cancel_status")
        memo = st.text_area("客人備註", key="vipcal_cancel_memo")
        if cal_row and st.button("🛑 取消後台＋同步日曆暫停", key="vipcal_cancel_exec", type="primary", use_container_width=True):
            try:
                from cancel_order import cancel_orders
                purchase_id = str(source.get("purchase_id") or "") or re.sub(r"\D", "", str(source.get("order_no") or ""))
                rows = cancel_orders(env_name, backend_email, backend_password, [{"purchase_id": purchase_id, "order_no": source.get("order_no", ""), "phone": customer.get("phone", ""), "service_date": source.get("date", ""), "period": source.get("time", "")}], cancel_status, memo, "", "")
                if not rows or not rows[0].get("ok"):
                    st.error((rows[0].get("message") if rows else "後台取消失敗")); return
                old_date = datetime.strptime(str(cal_row.get("date")), "%Y-%m-%d").date()
                _patch_calendar(vcs, vcp, cal_row, old_date, cal_row.get("period", ""), "保持不變", "綠色／暫停")
                _refresh(vcs, st, env_name, backend_email, backend_password, customer)
                st.success("✅ 後台已取消，選取的 Google 日曆已改為綠色／暫停")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        return

    if action == "僅新增日曆":
        new_date, new_period = _period_input(st, vcs, source, "vipcal_add_only", "新增日曆日期", "新增日曆時段")
        if st.button("🟣 新增紫色／未安排日曆", key="vipcal_add_only_exec", type="primary", use_container_width=True):
            try:
                _create_calendar_direct(vcs, customer, source, new_date.isoformat(), new_period, arranged=False)
                _refresh(vcs, st, env_name, backend_email, backend_password, customer)
                st.success("✅ 已新增紫色／未安排 Google 日曆")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        return

    if action == "先預約再新增日曆":
        new_date, new_period = _period_input(st, vcs, source, "vipcal_book_add", "預約日期", "預約時段")
        if st.button("🔎 先確認後台可預約", key="vipcal_book_check", use_container_width=True):
            try:
                st.session_state.vipcal_book_ok = vcs.check_backend_change_slot(customer, source, new_date.isoformat(), new_period)
            except Exception as exc:
                st.error(str(exc))
        check = st.session_state.get("vipcal_book_ok")
        if check and check.get("available"):
            st.success(f"可預約：{check.get('staff') or '有可用時段'}")
            if st.button("🟡 成立訂單＋新增黃色／已安排日曆", key="vipcal_book_exec", type="primary", use_container_width=True):
                try:
                    result = vcs.create_backend_order_from_template(customer, source, new_date.isoformat(), new_period)
                    _create_calendar_direct(vcs, customer, source, new_date.isoformat(), new_period, arranged=True, order_no=result.get("order_no", ""))
                    _refresh(vcs, st, env_name, backend_email, backend_password, customer)
                    st.success(f"✅ 訂單 {result.get('order_no','')} 已成立，Google 日曆已新增黃色／已安排")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        return


def apply_patch(vcs, vcp):
    vcs.render_vip_calendar_sync = lambda backend_email, backend_password, env_name: _render_manual_ui(vcs, vcp, backend_email, backend_password, env_name)
    return vcs
