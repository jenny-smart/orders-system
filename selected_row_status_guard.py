# -*- coding: utf-8 -*-
"""指定列批次建單狀態防呆。

只有「未安排」且訂單編號空白的列可以進入建單。
待確認／已安排／暫停／保留單一律不執行。
同時清除 Google Sheet 可能帶入的不可見空白字元。
"""
from __future__ import annotations

import re
import pandas as pd
import orders

_INSTALLED = False


def normalize_status(value) -> str:
    text = str(value or "")
    text = text.replace("\u00a0", " ").replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", "", text)


def _first_series(df: pd.DataFrame, name: str) -> pd.Series:
    """Google Sheet 若有重複欄名，只讀第一個同名欄，避免 pandas reindex error。"""
    selected = df.loc[:, df.columns == name]
    if selected.shape[1] == 0:
        raise RuntimeError(f"工作表缺少必要欄位：{name}")
    return selected.iloc[:, 0]


def _is_unarranged_blank_order(row) -> bool:
    return normalize_status(row.get("狀態", "")) == "未安排" and orders.is_blank(row.get("訂單編號", ""))


def _safe_load_candidates(batch_opt, sheet_name: str) -> pd.DataFrame:
    """直接重建優化候選資料，不再在原 DataFrame 上二次 reindex。"""
    try:
        _, df = batch_opt.load_worksheet(sheet_name)
    except Exception as exc:
        if type(exc).__name__ == "WorksheetNotFound":
            raise ValueError(f"找不到工作表分頁「{sheet_name}」") from exc
        raise

    if "__sheet_row__" not in df.columns:
        df = df.copy()
        df["__sheet_row__"] = range(2, len(df) + 2)

    work = pd.DataFrame(index=df.index)
    work["__sheet_row__"] = _first_series(df, "__sheet_row__")
    for col in batch_opt.REQUIRED_COLUMNS:
        work[col] = _first_series(df, col).map(batch_opt._text)

    work = work[
        work["狀態"].map(normalize_status).eq("未安排")
        & work["訂單編號"].eq("")
        & work["姓名"].ne("")
        & work["電話"].ne("")
        & work["地址"].ne("")
        & work["日期"].ne("")
        & work["開始時間"].ne("")
        & work["結束時間"].ne("")
    ].copy()
    work.reset_index(drop=True, inplace=True)
    work["日期顯示"] = work["日期"].map(batch_opt._date_text)
    work["時段顯示"] = work.apply(
        lambda r: f"{batch_opt._time_text(r['開始時間'])}-{batch_opt._time_text(r['結束時間'])}", axis=1
    )
    work["群組鍵"] = work.apply(
        lambda r: (batch_opt._text(r["姓名"]), batch_opt._text(r["電話"]), batch_opt._text(r["地址"])), axis=1
    )
    return work


def install_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    orders.should_process_row = _is_unarranged_blank_order
    orders.should_create_order = _is_unarranged_blank_order

    try:
        import batch_booking_optimized as batch_opt
        batch_opt._load_candidates = lambda sheet_name: _safe_load_candidates(batch_opt, sheet_name)
    except Exception:
        pass

    _INSTALLED = True
