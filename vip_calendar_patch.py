# -*- coding: utf-8 -*-
"""
Runtime patch for vip_calendar_sync.py.

Purpose:
- Before creating a yellow calendar event for a newly-created VIP order,
  search the customer's nearby existing calendar events.
- Prefer a purple prebook event (e.g. <每月確認/自行預約>) with the same phone,
  similar date, same time period, and optionally same address.
- If found, update that event in-place: move to the new date/time, change
  <每月確認/自行預約> to <已確認/自行預約>, set yellow, and add order no.
- Only insert a brand new yellow event when no suitable prebook event exists.

This file avoids touching the large existing ordersapp.py / quick_order.py files.
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
        is_prebook = "每月確認/自行預約" in summary or "每月確認/自行預約" in description or "VIP預排" in description

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
    # Require a strong-enough match: phone + at least prebook/purple/same period signal.
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
        # Calendar lookup is best-effort: do not block backend lookup if local secrets are missing.
        try:
            service = vcs.build_calendar_service()
            all_events = []
            seen = set()
            for addr in data.get("addresses") or [""]:
                region = vcs.orders.get_region_by_address(addr, vcs.ACCOUNTS) if addr else "台北"
                region = region or "台北"
                calendar_id = vcs._calendar_id(region)
                anchor = datetime.now(vcs.TAIPEI_TZ)
                events = vcs._list_events(service, calendar_id, anchor - timedelta(days=120), anchor + timedelta(days=180))
                for ev in events:
                    blob = _norm(" ".join([ev.get("summary", ""), ev.get("description", ""), ev.get("location", "")]))
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

        # Yellow + order_no means a backend order has been created. Reuse nearby purple/prebook event first.
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
                updated["_vip_original_date"] = (
                    _event_dt(vcs, candidate).strftime("%Y-%m-%d") if _event_dt(vcs, candidate) else ""
                )
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
