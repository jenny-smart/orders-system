# -*- coding: utf-8 -*-
"""付款後 5 碼／星和診所對帳；刻意與 atm.py 完全分離。"""
import json
import os
import re
from datetime import datetime
from itertools import combinations
from typing import Callable, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from . import memo


REGION_SECRET_PREFIX = {
    "台北": "TAIPEI",
    "台中": "TAICHUNG",
    "桃園": "TAOYUAN",
    "新竹": "HSINCHU",
    "高雄": "KAOHSIUNG",
}
WORKSHEET_TITLE = "ATM"
STAR_CLINIC = "星和診所"


def _logger(callback: Optional[Callable[[str], None]] = None):
    def log(message):
        message = str(message)
        print(message, flush=True)
        if callback:
            callback(message)
    return log


def _client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    info = None
    try:
        import streamlit as st
        for key in ("gcp_service_account", "GOOGLE_SERVICE_ACCOUNT"):
            if key in st.secrets:
                info = dict(st.secrets[key])
                break
    except Exception:
        pass
    if info is None and os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip():
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    if info is not None:
        return gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
    return gspread.authorize(Credentials.from_service_account_file(memo.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=scopes))


def _secret_text(key: str) -> str:
    try:
        import streamlit as st
        value = st.secrets.get(key, "")
        if value is not None and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return str(os.getenv(key, "") or "").strip()


def _sheet_config(region: str) -> Dict:
    if region not in REGION_SECRET_PREFIX:
        raise ValueError(f"不支援的地區：{region}")
    prefix = REGION_SECRET_PREFIX[region]
    spreadsheet_id = _secret_text(f"ATM_{prefix}_SPREADSHEET_ID")
    gid_text = _secret_text(f"ATM_{prefix}_GID")
    worksheet_title = _secret_text(f"ATM_{prefix}_WORKSHEET_TITLE")
    if not spreadsheet_id:
        raise ValueError(f"Secrets 尚未設定「{region}」付款比對試算表 ID")
    try:
        gid = int(gid_text) if gid_text else None
    except ValueError as exc:
        raise ValueError(f"Secrets 的「{region}」GID 必須是整數") from exc
    if gid is None and not worksheet_title:
        if region in {"台北", "台中"}:
            worksheet_title = WORKSHEET_TITLE
        else:
            raise ValueError(f"Secrets 尚未設定「{region}」付款比對分頁 GID 或名稱")
    return {"spreadsheet_id": spreadsheet_id, "gid": gid, "worksheet_title": worksheet_title}


def get_worksheet(region: str):
    config = _sheet_config(region)
    spreadsheet = _client().open_by_key(config["spreadsheet_id"])
    if config["gid"] is not None:
        worksheet = spreadsheet.get_worksheet_by_id(int(config["gid"]))
        if worksheet is None:
            raise ValueError(f"找不到「{region}」指定分頁 gid={config['gid']}")
        return worksheet
    return spreadsheet.worksheet(config["worksheet_title"])


def extract_purchase_list(html: str) -> Optional[Dict]:
    marker = html.find("purchaseList:")
    start = html.find("{", marker) if marker >= 0 else -1
    if start < 0:
        return None
    depth = 0
    in_string = escaped = False
    for pos in range(start, len(html)):
        char = html[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:pos + 1])
                except (TypeError, ValueError):
                    return None
    return None


def _service_category(item: Dict) -> str:
    clean_type = str(item.get("clean_type_id") or "")
    if clean_type in {"5", "6"}:
        return "家電清潔"
    if clean_type in {"7", "8"}:
        return "水洗清潔"
    return "居家清潔"


def _money(value) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _row(item: Dict) -> Dict:
    service_date = str(item.get("date_clean") or "")[:10]
    return {
        "service_month": service_date[:7].replace("-", "."),
        "order_no": str(item.get("order_no") or ""),
        "name": str(item.get("name") or ""),
        "service_date": service_date,
        "net_amount": _money(item.get("total")) - _money(item.get("fare")),
        "last5": str(item.get("account5") or "").strip(),
        "service_category": _service_category(item),
        "service_detail": "清潔服務",
        "paid_at": str(item.get("paid_at") or "")[:16],
    }


def search_orders(session, paid_start: str, paid_end: str, payment_status: str,
                  ui_logger=None) -> List[Dict]:
    """依付款日期、付款狀態與 ATM 搜尋所有分頁，星和診所亦全數保留。"""
    if payment_status not in {"0", "1"}:
        raise ValueError("付款狀態必須是待付款或已付款")
    log = _logger(ui_logger)
    params = {
        "paid_at_s": paid_start, "paid_at_e": paid_end,
        "payway": "2", "purchase_status": payment_status, "p_board": "on",
    }
    output, seen, skipped_zero = [], set(), 0
    page = 1
    while True:
        params["page"] = page
        response = memo.session_get(session, f"{memo.BASE_URL}/purchase", params=params)
        response.raise_for_status()
        payload = extract_purchase_list(response.text)
        if not payload:
            if page == 1:
                log("查無訂單，或後台頁面格式已變更")
            break
        for item in payload.get("data", []):
            row = _row(item)
            if row["net_amount"] == 0:
                skipped_zero += 1
                continue
            if row["order_no"] and row["order_no"] not in seen:
                seen.add(row["order_no"])
                output.append(row)
        last_page = int(payload.get("last_page") or 1)
        log(f"讀取第 {page}/{last_page} 頁")
        if page >= last_page:
            break
        page += 1
    output.sort(key=lambda row: (row["paid_at"], row["order_no"]))
    if skipped_zero:
        log(f"略過 {skipped_zero} 筆總金額扣車馬費為 0 的訂單")
    log(f"共取得 {len(output)} 筆；其中星和診所 {sum(r['name'] == STAR_CLINIC for r in output)} 筆")
    return output


def _values(row: Dict) -> List:
    return [row[key] for key in (
        "service_month", "order_no", "name", "service_date", "net_amount",
        "last5", "service_category", "service_detail", "paid_at",
    )]


def paste_orders(region: str, rows: List[Dict], ui_logger=None) -> Dict:
    """從銀行 B 欄最後資料列下方 5 列開始，獨立貼至 K:S。"""
    log = _logger(ui_logger)
    rows = [row for row in rows if _money(row.get("net_amount")) != 0]
    if not rows:
        log("沒有非零淨額的資料可以貼入 K:S")
        return {"pasted": 0, "start_row": None}
    ws = get_worksheet(region)
    values = memo.with_retry(ws.get_all_values)
    last_bank_row = max((i for i, row in enumerate(values, 1)
                         if len(row) >= 2 and str(row[1]).strip()), default=1)
    start = last_bank_row + 5
    while start <= len(values) and any(str(v).strip() for v in values[start - 1][10:19]):
        start += 5
    payload = [_values(row) for row in rows]
    memo.with_retry(ws.update, f"K{start}:S{start + len(payload) - 1}", payload, value_input_option="RAW")
    log(f"已貼上 {len(payload)} 筆至 K{start}:S{start + len(payload) - 1}")
    return {"pasted": len(payload), "start_row": start}


def _amount(value) -> Optional[int]:
    text = re.sub(r"[^0-9.-]", "", str(value or ""))
    try:
        return int(round(float(text))) if text else None
    except ValueError:
        return None


def _date(value: str) -> Optional[datetime]:
    digits = re.sub(r"\D", "", str(value or ""))
    for size, fmt in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"), (8, "%Y%m%d")):
        if len(digits) >= size:
            try:
                return datetime.strptime(digits[:size], fmt)
            except ValueError:
                pass
    return None


def _identity_match(note: str, row: Dict) -> bool:
    compact = re.sub(r"\s", "", str(note or ""))
    code = re.sub(r"\D", "", row.get("last5", ""))
    return bool((code and re.sub(r"\D", "", compact).endswith(code))
                or (row["name"] and row["name"] in compact)
                or row["name"] == STAR_CLINIC and STAR_CLINIC in compact)


def _find_group(income: int, note: str, bank_time: str, candidates: List[Dict]) -> List[Dict]:
    bank_dt = _date(bank_time)
    eligible = []
    for row in candidates:
        if not _identity_match(note, row):
            continue
        paid_dt = _date(row["paid_at"])
        if bank_dt and paid_dt and paid_dt < bank_dt:
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: (_date(row["paid_at"]) or datetime.max, row["order_no"]))
    for size in range(1, min(8, len(eligible)) + 1):
        groups = [group for group in combinations(eligible, size)
                  if sum(row["net_amount"] for row in group) == income]
        if len(groups) == 1:
            return list(groups[0])
        if len(groups) > 1:
            return []
    return []


def match_bank_rows(region: str, ui_logger=None) -> Dict:
    """以 F 欄收入、H 欄備註、B 欄日期，比對 K:S；支援多單金額加總。"""
    log = _logger(ui_logger)
    ws = get_worksheet(region)
    sheet = memo.with_retry(ws.get_all_values)
    last_bank_row = max((i for i, row in enumerate(sheet, 1)
                         if len(row) >= 2 and str(row[1]).strip()), default=1)
    candidates = []
    for i, raw in enumerate(sheet, 1):
        if i <= last_bank_row or len(raw) < 19 or not str(raw[11]).strip():
            continue
        candidates.append({
            "source_row": i, "service_month": raw[10], "order_no": raw[11],
            "name": raw[12], "service_date": raw[13], "net_amount": _amount(raw[14]) or 0,
            "last5": raw[15], "service_category": raw[16], "service_detail": raw[17],
            "paid_at": raw[18],
        })
    used, updates, matched, matched_banks, review = set(), [], 0, 0, 0
    for bank_row in range(2, last_bank_row + 1):
        raw = sheet[bank_row - 1]
        income = _amount(raw[5] if len(raw) > 5 else "")  # F
        note = raw[7] if len(raw) > 7 else ""              # H
        bank_time = raw[1] if len(raw) > 1 else ""         # B
        if not income or (len(raw) > 11 and str(raw[11]).strip()):
            continue
        group = _find_group(income, note, bank_time, [c for c in candidates if c["order_no"] not in used])
        if not group:
            review += 1
            continue
        target_rows = list(range(bank_row, bank_row + len(group)))
        if any(r <= len(sheet) and any(str(v).strip() for v in sheet[r - 1][10:19]) for r in target_rows):
            review += 1
            continue
        if any(r != bank_row and r <= last_bank_row and any(str(v).strip() for v in sheet[r - 1][1:8]) for r in target_rows):
            review += 1
            log(f"第 {bank_row} 列為多訂單，但下方銀行列非空，留待人工確認")
            continue
        for target, candidate in zip(target_rows, group):
            updates.append({"range": f"K{target}:S{target}", "values": [[*_values(candidate)]]})
            updates.append({"range": f"K{candidate['source_row']}:S{candidate['source_row']}", "values": [[""] * 9]})
            used.add(candidate["order_no"])
            matched += 1
        matched_banks += 1
        log(f"第 {bank_row} 列配對 {len(group)} 筆，合計 {income}")
    if updates:
        memo.with_retry(ws.batch_update, updates, value_input_option="RAW")
    return {"matched_orders": matched, "matched_bank_rows": matched_banks, "review": review}
