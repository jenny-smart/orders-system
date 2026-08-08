# -*- coding: utf-8 -*-
"""Second-stage VIP calendar patch.

- Fetch every Google Calendar page inside the selected month/date range.
  The old helper only read the first 250 calendar events, which could omit later
  dates such as 8/28 on a busy regional calendar.
- Replace the side-by-side backend/calendar comparison with one chronological
  calendar-style agenda containing both backend orders and Google Calendar rows.
"""

from collections import defaultdict


def _norm(value):
    import re
    return re.sub(r"\s+", "", str(value or ""))


def _paginated_list_events(vcs, service, calendar_id, start_dt, end_dt):
    rows = []
    page_token = None
    page_count = 0
    while True:
        kwargs = {
            "calendarId": calendar_id,
            "timeMin": start_dt.isoformat(),
            "timeMax": end_dt.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 250,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.events().list(**kwargs).execute()
        rows.extend(result.get("items", []) or [])
        page_count += 1
        page_token = result.get("nextPageToken")
        if not page_token or page_count >= 20:
            break
    return rows


def _status_and_icon(vcs, vcp, row):
    event = row.get("event") or {}
    icon, color_name = vcp._color_meta(vcs, row.get("color_id"))
    status = vcp._confirmation_status(event)
    return icon, color_name, status


def _calendar_agenda_renderer(vcs, vcp, customer, source):
    st = vcs.st
    st.divider()
    st.markdown("## 查詢行程（日曆檢視）")
    st.caption(
        f"{customer.get('query_date_s', '')} ～ {customer.get('query_date_e', '')}｜"
        "同一天會一起顯示後台已付款訂單與 Google 日曆。"
    )

    matched = vcp._find_matching_calendar_row(vcs, customer, source)
    by_date = defaultdict(list)

    for order in customer.get("orders") or []:
        date_s = str(order.get("date") or "")
        if not date_s:
            continue
        by_date[date_s].append({
            "kind": "backend",
            "period": str(order.get("time") or "").replace(" ", ""),
            "order_no": str(order.get("order_no") or ""),
            "address": str(order.get("address") or ""),
            "payway": str(order.get("payway") or ""),
            "raw": order,
        })

    for row in customer.get("calendar_events") or []:
        date_s = str(row.get("date") or "")
        if not date_s:
            continue
        by_date[date_s].append({
            "kind": "calendar",
            "period": str(row.get("period") or "").replace(" ", ""),
            "summary": str(row.get("summary") or ""),
            "raw": row,
        })

    if not by_date:
        if customer.get("calendar_lookup_error"):
            st.warning(f"Google 日曆查詢失敗：{customer.get('calendar_lookup_error')}")
        else:
            st.info("此查詢範圍沒有後台訂單或 Google 日曆事件")
        return matched

    weekdays = "一二三四五六日"
    from datetime import datetime

    for date_s in sorted(by_date):
        items = sorted(by_date[date_s], key=lambda x: (x.get("period", ""), x.get("kind", "")))
        try:
            d = datetime.strptime(date_s, "%Y-%m-%d").date()
            date_label = f"{d.month}/{d.day}（{weekdays[d.weekday()]}）"
        except Exception:
            date_label = date_s

        backend_count = sum(x["kind"] == "backend" for x in items)
        calendar_count = sum(x["kind"] == "calendar" for x in items)
        with st.expander(
            f"📅 {date_label}　後台 {backend_count}｜日曆 {calendar_count}",
            expanded=True,
        ):
            for item in items:
                if item["kind"] == "backend":
                    order = item["raw"]
                    marker = "⭐ " if order is source else ""
                    st.markdown(
                        f"{marker}**🧾 後台｜{item['period'] or '未標時段'}｜{item['order_no']}**  "
                        f"\n{item['address']}｜{item['payway']}"
                    )
                else:
                    row = item["raw"]
                    event = row.get("event") or {}
                    icon, color_name, status = _status_and_icon(vcs, vcp, row)
                    marker = "⭐ " if matched is row else ""
                    st.markdown(
                        f"{marker}**{icon} 日曆｜{item['period'] or '未標時段'}｜{status}**  "
                        f"\n{item['summary']}  "
                        f"\n{color_name}｜Event ID `{event.get('id', '')}`"
                    )

    if customer.get("calendar_lookup_error"):
        st.warning(f"Google 日曆查詢有錯誤：{customer.get('calendar_lookup_error')}")

    # Keep a compact selected-order comparison below the agenda because later
    # actions need to know whether the chosen backend order already has a match.
    st.markdown("### 目前選取訂單")
    st.write(
        f"**{source.get('order_no', '')}｜{source.get('date', '')} "
        f"{str(source.get('time') or '').replace(' ', '')}**"
    )
    if matched:
        event = matched.get("event") or {}
        icon, color_name, status = _status_and_icon(vcs, vcp, matched)
        st.write(
            f"配對日曆：**{matched.get('date', '')} {matched.get('period', '')}｜"
            f"{icon} {status}｜{color_name}**"
        )
        diffs = []
        if str(source.get("date") or "") != str(matched.get("date") or ""):
            diffs.append(f"日期：後台 {source.get('date', '')} / 日曆 {matched.get('date', '')}")
        backend_period = str(source.get("time") or "").replace(" ", "")
        if backend_period != str(matched.get("period") or "").replace(" ", ""):
            diffs.append(f"時段：後台 {backend_period} / 日曆 {matched.get('period', '')}")
        if diffs:
            st.warning("⚠️ 發現差異：" + "；".join(diffs))
        else:
            st.success("✅ 目前選取訂單與配對日曆日期、時段一致")
        if event.get("summary"):
            st.caption(event.get("summary"))
    else:
        st.warning("目前選取的後台訂單找不到可直接配對的 Google 日曆事件")

    return matched


def apply_patch(vcs, vcp):
    """Apply after vip_calendar_patch.apply_patch(vcs)."""
    vcs._list_events = lambda service, calendar_id, start_dt, end_dt: _paginated_list_events(
        vcs, service, calendar_id, start_dt, end_dt
    )

    # _render_compare_first_ui in vip_calendar_patch resolves this global name at runtime.
    vcp._render_query_results = lambda vcs_arg, customer, source: _calendar_agenda_renderer(
        vcs_arg, vcp, customer, source
    )
    return vcs
