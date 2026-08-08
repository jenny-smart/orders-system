# -*- coding: utf-8 -*-
"""
Runtime patch for vip_calendar_sync.py.

Adds two behaviors without touching the large ordersapp.py / quick_order.py files:
1. VIP lookup uses the query range selected by the test UI, and shows only backend
   orders + Google Calendar events inside that range.
2. A newly-created yellow VIP order reuses a nearby purple prebook event when
   possible, moving it to the confirmed date/time instead of creating a duplicate.
"""

from datetime import datetime, time, timedelta


def _norm(value):
    import re
    return re.sub(r"\s+", "", str(value or ""))


def _event_dt(vcs, event):
    raw = (event.get("start") or {}).get("dateTime")
    if not raw:
        return None
    try:
        dt = vcs.orders.parse_event_time(raw)
        return dt.astimezone(vcs.TAIPEI_TZ) if dt else None
    except Exception:
        return None


def _event_end_dt(vcs, event):
    raw = (event.get("end") or {}).get("dateTime")
    if not raw:
        return None
    try:
        dt = vcs.orders.parse_event_time(raw)
        return dt.astimezone(vcs.TAIPEI_TZ) if dt else None
    except Exception:
        return None


def _replace_confirm_status(text):
    text = str(text or "")
    replacements = [
        ("<每月確認/自行預約>", "<已確認/自行預約>"),
        ("＜每月確認/自行預約＞", "＜已確認/自行預約＞"),
        ("每月確認/自行預約", "已確認/自行預約"),
    ]
    for old, new in replacements:
        if old in text:
            return text.replace(old, new)
    return text


def _query_range(vcs):
    """Read YYYY-MM-DD query range selected by the Streamlit test UI."""
    today = datetime.now(vcs.TAIPEI_TZ).date()
    default_s = today.replace(day=1)
    default_e = default_s + timedelta(days=62)
    try:
        date_s = str(vcs.st.session_state.get("vipcal_query_date_s") or default_s.isoformat())
        date_e = str(vcs.st.session_state.get("vipcal_query_date_e") or default_e.isoformat())
        start_d = datetime.strptime(date_s, "%Y-%m-%d").date()
        end_d = datetime.strptime(date_e, "%Y-%m-%d").date()
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        return start_d, end_d
    except Exception:
        return default_s, default_e


def find_nearby_prebook_event(vcs, service, calendar_id, phone, address, new_date_s, new_period_s, window_days=14):
    """Return best nearby purple/prebook calendar event for this VIP customer."""
    target_date = datetime.strptime(new_date_s, "%Y-%m-%d").date()
    start_dt = datetime.combine(target_date - timedelta(days=window_days), time.min, tzinfo=vcs.TAIPEI_TZ)
    end_dt = datetime.combine(target_date + timedelta(days=window_days + 1), time.min, tzinfo=vcs.TAIPEI_TZ)
    events = vcs._list_events(service, calendar_id, start_dt, end_dt)

    phone_n = _norm(phone)
    addr_n = _norm(address)
    p_start, p_end = str(new_period_s).replace(" ", "").split("-", 1)

    scored = []
    for event in events:
        summary = str(event.get("summary") or "")
        description = str(event.get("description") or "")
        location = str(event.get("location") or "")
        blob = _norm(" ".join([summary, description, location]))
        if phone_n and phone_n not in blob:
            continue

        start = _event_dt(vcs, event)
        end = _event_end_dt(vcs, event)
        if not start or not end:
            continue
        date_diff = abs((start.date() - target_date).days)
        if date_diff > window_days:
            continue

        event_period = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
        same_period = event_period == f"{p_start}-{p_end}"
        same_addr = bool(addr_n and addr_n in blob)
        is_purple = str(event.get("colorId") or "") == str(vcs.COLOR_PURPLE)
        is_prebook = (
            "每月確認/自行預約" in summary
            or "每月確認/自行預約" in description
            or "VIP預排" in description
        )

        score = 0
        if is_prebook:
            score += 100
        if is_purple:
            score += 60
        if same_period:
            score += 40
        if same_addr:
            score += 20
        score += max(0, 20 - date_diff)
        scored.append((score, date_diff, start, event))

    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    best = scored[0]
    return best[3] if best[0] >= 40 else None


def update_prebook_to_confirmed(vcs, service, calendar_id, event, phone, address, new_date_s, new_period_s, order_no):
    start_dt, end_dt = vcs._event_range(new_date_s, new_period_s)
    summary = _replace_confirm_status(event.get("summary", ""))
    description = _replace_confirm_status(event.get("description", ""))
    description = vcs._append_status(description, f"已成單 {order_no}")
    if order_no and order_no not in _norm(description):
        description = (description + f"\n訂單編號：{order_no}").strip()
    if phone and phone not in _norm(description):
        description = (description + f"\n電話：{phone}").strip()

    body = {
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Taipei"},
        "colorId": str(vcs.COLOR_YELLOW),
        "summary": summary,
        "description": description,
    }
    if address and not event.get("location"):
        body["location"] = address

    return service.events().patch(
        calendarId=calendar_id,
        eventId=event["id"],
        body=body,
    ).execute()


def apply_patch(vcs):
    """Patch functions inside imported vip_calendar_sync module."""
    original_create = vcs.create_or_copy_calendar_event
    original_load = vcs.load_vip_customer

    def load_vip_customer_with_calendar(env_name, backend_email, backend_password, phone, clean_type_id="1"):
        data = original_load(env_name, backend_email, backend_password, phone, clean_type_id)
        start_d, end_d = _query_range(vcs)
        start_s, end_s = start_d.isoformat(), end_d.isoformat()

        # Restrict the backend rows displayed/used by the VIP page to the selected range.
        data["orders"] = [
            row for row in (data.get("orders") or [])
            if row.get("date") and start_s <= str(row.get("date")) <= end_s
        ]
        data["query_date_s"] = start_s
        data["query_date_e"] = end_s

        # Calendar lookup uses the same selected date range, avoiding a large historical scan.
        try:
            service = vcs.build_calendar_service()
            all_events = []
            seen = set()
            range_start = datetime.combine(start_d, time.min, tzinfo=vcs.TAIPEI_TZ)
            range_end = datetime.combine(end_d + timedelta(days=1), time.min, tzinfo=vcs.TAIPEI_TZ)
            for addr in data.get("addresses") or [""]:
                region = vcs.orders.get_region_by_address(addr, vcs.ACCOUNTS) if addr else "台北"
                region = region or "台北"
                calendar_id = vcs._calendar_id(region)
                events = vcs._list_events(service, calendar_id, range_start, range_end)
                for ev in events:
                    blob = _norm(" ".join([
                        ev.get("summary", ""), ev.get("description", ""), ev.get("location", "")
                    ]))
                    if data.get("phone") and _norm(data["phone"]) not in blob:
                        continue
                    key = (calendar_id, ev.get("id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    start = _event_dt(vcs, ev)
                    end = _event_end_dt(vcs, ev)
                    all_events.append({
                        "calendar_id": calendar_id,
                        "event": ev,
                        "date": start.strftime("%Y-%m-%d") if start else "",
                        "period": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}" if start and end else "",
                        "summary": ev.get("summary", ""),
                        "color_id": str(ev.get("colorId") or ""),
                    })
            all_events.sort(key=lambda x: (x.get("date", ""), x.get("period", "")))
            data["calendar_events"] = all_events
            data["calendar_lookup_error"] = ""
        except Exception as exc:
            data["calendar_events"] = []
            data["calendar_lookup_error"] = str(exc)
        return data

    def create_or_update_calendar_event(region, phone, address, new_date_s, new_period_s, color_id, order_no="", name="", reference_event=None):
        service = vcs.build_calendar_service()
        calendar_id = vcs._calendar_id(region)

        if str(color_id) == str(vcs.COLOR_YELLOW) and order_no:
            candidate = find_nearby_prebook_event(
                vcs, service, calendar_id, phone, address, new_date_s, new_period_s
            )
            if candidate:
                updated = update_prebook_to_confirmed(
                    vcs, service, calendar_id, candidate, phone, address,
                    new_date_s, new_period_s, order_no,
                )
                updated["_vip_sync_action"] = "updated_existing_prebook"
                original_dt = _event_dt(vcs, candidate)
                updated["_vip_original_date"] = original_dt.strftime("%Y-%m-%d") if original_dt else ""
                return updated

        created = original_create(
            region=region, phone=phone, address=address,
            new_date_s=new_date_s, new_period_s=new_period_s,
            color_id=color_id, order_no=order_no, name=name,
            reference_event=reference_event,
        )
        if isinstance(created, dict):
            created["_vip_sync_action"] = "created_new_event"
        return created

    vcs.load_vip_customer = load_vip_customer_with_calendar
    vcs.create_or_copy_calendar_event = create_or_update_calendar_event
    return vcs
