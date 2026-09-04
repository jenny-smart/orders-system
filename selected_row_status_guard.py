# -*- coding: utf-8 -*-
"""批次建單共用防呆：欄位去重、候選條件、回填欄位定位、優化入口綁定。"""
from __future__ import annotations

import re
import pandas as pd
import orders

_INSTALLED = False
_ORIGINAL_LOAD_WORKSHEET = orders.load_worksheet
_ORIGINAL_UPDATE_SHEET_ROWS = orders.update_sheet_rows


def _scalar(value):
    if isinstance(value, pd.Series):
        return value.iloc[0] if len(value) else ""
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value


def normalize_status(value) -> str:
    text = str(_scalar(value) or "")
    text = text.replace("\u00a0", " ").replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", "", text)


def _dedupe_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.columns.is_unique:
        return df
    return df.loc[:, ~df.columns.duplicated(keep="first")].copy()


def _load_worksheet_unique(sheet_name: str):
    ws, df = _ORIGINAL_LOAD_WORKSHEET(sheet_name)
    return ws, _dedupe_dataframe(df)


def _first_series(df: pd.DataFrame, name: str) -> pd.Series:
    selected = df.loc[:, df.columns == name]
    if selected.shape[1] == 0:
        raise RuntimeError(f"工作表缺少必要欄位：{name}")
    return selected.iloc[:, 0]


def _should_process_row(row) -> bool:
    """已有單號可同步；沒有單號時只有未安排可進入建單流程。"""
    order_no = str(_scalar(row.get("訂單編號", "")) or "").strip()
    if order_no:
        return True
    return normalize_status(row.get("狀態", "")) == "未安排"


def _should_create_order(row) -> bool:
    order_no = str(_scalar(row.get("訂單編號", "")) or "").strip()
    return not order_no and normalize_status(row.get("狀態", "")) == "未安排"


def _safe_load_candidates(batch_opt, sheet_name: str) -> pd.DataFrame:
    """人工優化候選：未安排空白單號可建；已有單號可指定做同步。"""
    try:
        _, df = _load_worksheet_unique(sheet_name)
    except Exception as exc:
        if type(exc).__name__ == "WorksheetNotFound":
            raise ValueError(f"找不到工作表分頁「{sheet_name}」") from exc
        raise

    work = pd.DataFrame(index=df.index)
    work["__sheet_row__"] = _first_series(df, "__sheet_row__")
    for col in batch_opt.REQUIRED_COLUMNS:
        work[col] = _first_series(df, col).map(batch_opt._text)
    for col in ("原因", "沒班表日期"):
        work[col] = _first_series(df, col).map(batch_opt._text) if col in df.columns else ""

    required_ok = (
        work["姓名"].ne("") & work["電話"].ne("") & work["地址"].ne("")
        & work["日期"].ne("") & work["開始時間"].ne("") & work["結束時間"].ne("")
    )
    create_ok = work["狀態"].map(normalize_status).eq("未安排") & work["訂單編號"].eq("")
    existing_ok = work["訂單編號"].ne("")
    work = work[required_ok & (create_ok | existing_ok)].copy()

    work.reset_index(drop=True, inplace=True)
    work["日期顯示"] = work["日期"].map(batch_opt._date_text)
    work["時段顯示"] = work.apply(
        lambda r: f"{batch_opt._time_text(r['開始時間'])}-{batch_opt._time_text(r['結束時間'])}", axis=1
    )
    work["群組鍵"] = work.apply(
        lambda r: (batch_opt._text(r["姓名"]), batch_opt._text(r["電話"]), batch_opt._text(r["地址"])), axis=1
    )
    return work


def _update_sheet_rows_first_header(ws, row_results):
    """重複欄名時固定寫第一個正式欄位，避免訂單編號寫到後方同名欄。"""
    headers = orders.ensure_columns_in_sheet(ws)
    header_index = {}
    for i, header in enumerate(headers, 1):
        if header and header not in header_index:
            header_index[header] = i

    updates = []
    for row_num, info in (row_results or {}).items():
        xyz = orders.finalize_xyz(
            {
                "服務人員": info.get("服務人員", ""),
                "服務狀態": info.get("服務狀態", ""),
                "車馬費": info.get("車馬費", ""),
            },
            fallback_fare=info.get("車馬費", "0"),
        )
        info["服務人員"] = xyz["服務人員"]
        info["服務狀態"] = xyz["服務狀態"]
        info["車馬費"] = xyz["車馬費"]

        for key, value in info.items():
            if key not in header_index:
                continue
            if key == "狀態" and str(value).strip() not in ("已安排", "待確認"):
                continue
            updates.append({
                "range": orders.gspread.utils.rowcol_to_a1(int(row_num), header_index[key]),
                "values": [["" if value is None else str(value)]],
            })
    if updates:
        ws.batch_update(updates)
        orders.set_customer_notice_clip_style(ws, headers=headers, row_numbers=row_results.keys())


def install_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    orders.load_worksheet = _load_worksheet_unique
    orders.update_sheet_rows = _update_sheet_rows_first_header
    orders.should_process_row = _should_process_row
    orders.should_create_order = _should_create_order
    orders.ORDERS_VERSION = "v2026.09.05-2"
    orders.ORDERS_UPDATED_AT = "2026-09-05"

    try:
        import batch_booking_optimized as batch_opt
        import batch_booking_safety as batch_safety

        batch_opt.load_worksheet = _load_worksheet_unique
        batch_opt._load_candidates = lambda sheet_name: _safe_load_candidates(batch_opt, sheet_name)

        # safety 模組載入較早時會保存舊函式，這裡同步更新，確保斷點/復原也寫第一個正式欄。
        batch_safety._BASE_UPDATE_SHEET_ROWS = _update_sheet_rows_first_header
        batch_safety._orders.update_sheet_rows = _update_sheet_rows_first_header

        def _optimized_runner_with_lemon_fallback(**kwargs):
            kwargs["allow_auto_lemon_shift"] = True
            return batch_safety.run_process_web_optimized(**kwargs)

        batch_opt.run_process_web = _optimized_runner_with_lemon_fallback
    except Exception:
        pass

    _INSTALLED = True
