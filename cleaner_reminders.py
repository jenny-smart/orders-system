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
