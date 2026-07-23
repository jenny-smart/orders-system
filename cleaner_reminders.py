# -*- coding: utf-8 -*-
"""已付款訂單的專員隔日上班提醒（後台唯讀）。"""

import re
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import orders
from weekend_reminders import _address, _configure_backend, _name_phone, _service_date_time


def extract_cleaner_names(lines):
    staff = orders._extract_staff_line(lines)
    if not staff or staff == "無人力":
        return []
    return [
        name.strip()
        for name in re.split(r"\s+[Xx×]\s+", staff)
        if name.strip() and name.strip() != "無人力"
    ]


def cleaner_profile_ids(raw_html, target_names):
    """由 /cleaner1 表格找專員名稱對應的 user id。"""
    wanted = {re.sub(r"\s+", "", name): name for name in target_names}
    found = {}
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for row in soup.find_all("tr"):
        compact = re.sub(r"\s+", "", row.get_text(" ", strip=True))
        matched = [original for key, original in wanted.items() if key and key in compact]
        if not matched:
            continue
        html = str(row)
        match = re.search(r"/user/edit/(\d+)", html, re.I)
        if not match:
            match = re.search(r"/cleaner1/(\d+)(?=[/'\"?#])", html, re.I)
        if match:
            for name in matched:
                found[name] = match.group(1)
    return found


def extract_cleaner_line(raw_html):
    soup = BeautifulSoup(raw_html or "", "html.parser")
    field = soup.select_one('input[name="line"], input#line')
    return str(field.get("value") or "").strip() if field else ""


def build_cleaner_message(name, service_date, jobs, reference_date=None):
    day = datetime.strptime(service_date, "%Y-%m-%d").date()
    weekdays = "一二三四五六日"
    day_text = f"{day.month}/{day.day}（{weekdays[day.weekday()]}）"
    reference_date = reference_date or datetime.now(ZoneInfo("Asia/Taipei")).date()
    opening = "提醒您明日有排班" if day == reference_date + timedelta(days=1) else f"提醒您 {day_text} 有排班"
    lines = [f"{name}專員您好，{opening}：", "", f"日期：{day_text}"]
    for idx, job in enumerate(sorted(jobs, key=lambda item: (item["service_time"], item["order_no"])), 1):
        lines.extend([
            "",
            f"{idx}. {job.get('service_time') or '時間待確認'}",
            f"地址：{job.get('address') or '請至後台確認'}",
            f"訂單：{job.get('order_no') or ''}",
        ])
    lines.extend(["", "請確認明日行程，收到後請回覆「收到」，謝謝。"])
    return "\n".join(lines)


def _resolve_cleaner_lines(session, base_url, names):
    roster = session.get(
        f"{base_url}/cleaner1",
        params={"area_id": "", "keyword": ""},
        headers=orders.HEADERS,
        allow_redirects=True,
    )
    ids = cleaner_profile_ids(roster.text if roster.status_code == 200 else "", names)
    for name in names:
        if name in ids:
            continue
        response = session.get(
            f"{base_url}/cleaner1",
            params={"area_id": "", "keyword": name},
            headers=orders.HEADERS,
            allow_redirects=True,
        )
        if response.status_code == 200:
            ids.update(cleaner_profile_ids(response.text, [name]))

    result = {}
    for name, user_id in ids.items():
        detail = session.get(
            f"{base_url}/user/edit/{user_id}",
            headers=orders.HEADERS,
            allow_redirects=True,
        )
        result[name] = {
            "user_id": user_id,
            "line_url": extract_cleaner_line(detail.text if detail.status_code == 200 else ""),
        }
    return result


def find_paid_cleaner_reminders(
    env_name, backend_email, backend_password, service_date, max_pages=20
):
    """查詢指定服務日的已付款訂單，依專員彙整並補上專員 LINE 連結。"""
    _configure_backend(env_name)
    session = orders.requests.Session()
    if not orders.login(session, backend_email, backend_password):
        raise RuntimeError("後台登入失敗，請確認帳號密碼")

    jobs_by_name = defaultdict(list)
    hit_page_limit = True
    for page in range(1, max_pages + 1):
        params = dict(orders.PURCHASE_FILTER_PARAMS_TEMPLATE)
        params.update({
            "clean_date_s": service_date,
            "clean_date_e": service_date,
            "purchase_status": "1",
            "p_board": "on",
            "page": str(page),
        })
        response = session.get(
            orders.PURCHASE_URL,
            params=params,
            headers=orders.HEADERS,
            allow_redirects=True,
        )
        if response.status_code != 200:
            hit_page_limit = False
            break
        blocks = orders.extract_order_cards_from_purchase_html(response.text)
        if not blocks:
            hit_page_limit = False
            break
        for block in blocks:
            lines = block.get("lines", [])
            joined = "\n".join(lines)
            if not re.search(r"付款狀態[：:]\s*已付款", joined):
                continue
            found_date, service_time = _service_date_time(lines)
            if found_date != service_date:
                continue
            customer_name, _ = _name_phone(lines)
            job = {
                "order_no": block.get("order_no", ""),
                "service_date": found_date,
                "service_time": service_time,
                "address": _address(lines),
                "customer_name": customer_name,
            }
            for cleaner_name in extract_cleaner_names(lines):
                jobs_by_name[cleaner_name].append(job)
        if len(blocks) < 20:
            hit_page_limit = False
            break

    profiles = _resolve_cleaner_lines(session, orders.BASE_URL, sorted(jobs_by_name))
    rows = []
    for name in sorted(jobs_by_name):
        profile = profiles.get(name, {})
        jobs = jobs_by_name[name]
        rows.append({
            "name": name,
            "user_id": profile.get("user_id", ""),
            "line_url": profile.get("line_url", ""),
            "service_date": service_date,
            "jobs": jobs,
            "message": build_cleaner_message(name, service_date, jobs),
        })
    return rows, {
        "base_url": orders.BASE_URL,
        "cleaner_count": len(rows),
        "job_count": sum(len(items) for items in jobs_by_name.values()),
        "hit_page_limit": hit_page_limit,
    }
