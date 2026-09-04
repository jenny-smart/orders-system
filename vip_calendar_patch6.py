# -*- coding: utf-8 -*-
"""VIP 日曆「每月確認」狀態防呆。

規則：
- 「每月確認」的安排狀態固定為「待確認」，底層仍使用 Google Calendar 紫色。
- 不允許「每月確認」同時被存成黃色／已安排或其他狀態。
- 舊事件若已是每月確認，畫面標籤顯示「待確認」；顏色不一致時顯示異常提示。
- 編輯既有每月確認事件時，即使確認文字選「保持不變」，也會把狀態修正為待確認／紫色。
"""
from __future__ import annotations


def _is_monthly_event(event: dict) -> bool:
    text = " ".join([
        str((event or {}).get("summary") or ""),
        str((event or {}).get("description") or ""),
    ])
    return "每月確認/自行預約" in text or "每月確認" in text


def apply_patch(vcs, vcp):
    import vip_calendar_patch3 as patch3
    import vip_calendar_patch4 as patch4

    # 「待確認」與「未安排」底層都使用紫色，但業務狀態分開顯示。
    patch4.COLOR_OPTIONS = [
        "保持不變", "紫色／待確認", "紫色／未安排", "黃色／已安排", "綠色／暫停"
    ]
    patch4.NEW_COLOR_OPTIONS = [
        "紫色／待確認", "紫色／未安排", "黃色／已安排", "綠色／暫停"
    ]

    original_color_meta = patch4._color_meta

    def _color_meta(vcs_arg, row):
        event = (row or {}).get("event") or {}
        if _is_monthly_event(event):
            cid = str((row or {}).get("color_id") or event.get("colorId") or "")
            if cid == str(vcs_arg.COLOR_PURPLE):
                return "🟣", "待確認"
            if cid == str(vcs_arg.COLOR_YELLOW):
                return "🟡", "待確認（顏色異常：應為紫色）"
            if cid == str(vcs_arg.COLOR_GREEN):
                return "🟢", "待確認（顏色異常：應為紫色）"
            return "⚪", "待確認（顏色異常：應為紫色）"
        return original_color_meta(vcs_arg, row)

    patch4._color_meta = _color_meta

    def _color_id(vcs_arg, choice):
        mapping = {
            "紫色／待確認": str(vcs_arg.COLOR_PURPLE),
            "紫色／未安排": str(vcs_arg.COLOR_PURPLE),
            "黃色／已安排": str(vcs_arg.COLOR_YELLOW),
            "綠色／暫停": str(vcs_arg.COLOR_GREEN),
        }
        return mapping[choice]

    patch4._color_id = _color_id

    original_calendar_fields = patch4._calendar_fields

    def _calendar_fields(
        st, vcs_arg, prefix, default_date, default_period, *,
        confirm_default="保持不變", color_default="保持不變", allow_keep=True,
    ):
        if confirm_default == "每月確認" and color_default == "紫色／未安排":
            color_default = "紫色／待確認"
        values = original_calendar_fields(
            st, vcs_arg, prefix, default_date, default_period,
            confirm_default=confirm_default,
            color_default=color_default,
            allow_keep=allow_keep,
        )
        cal_date, cal_period, confirmation, color = values
        if confirmation == "每月確認":
            color = "紫色／待確認"
            st.caption("防呆：確認文字為「每月確認」時，安排狀態固定為「待確認」（紫色）。")
        return cal_date, cal_period, confirmation, color

    patch4._calendar_fields = _calendar_fields

    original_create = patch4._create_calendar_direct

    def _create_calendar_direct(
        vcs_arg, customer, source, date_s, period_s, confirmation, color_choice, order_no=""
    ):
        if confirmation == "每月確認":
            color_choice = "紫色／待確認"
        return original_create(
            vcs_arg, customer, source, date_s, period_s,
            confirmation, color_choice, order_no=order_no,
        )

    patch4._create_calendar_direct = _create_calendar_direct

    original_update = patch3._update_calendar_schedule

    def _update_calendar_schedule(
        vcs_arg, vcp_arg, row, new_date, new_period, confirmation, color_choice
    ):
        event = (row or {}).get("event") or {}
        effective_monthly = confirmation == "每月確認" or (
            confirmation == "保持不變" and _is_monthly_event(event)
        )
        if effective_monthly:
            # patch3 尚未認識「待確認」字串，因此傳入既有紫色選項；底層 colorId 相同。
            color_choice = "紫色／未安排"
        return original_update(
            vcs_arg, vcp_arg, row, new_date, new_period, confirmation, color_choice
        )

    patch3._update_calendar_schedule = _update_calendar_schedule

    # 舊版共用更新入口也套同一層規則，避免其他路徑繞過 patch4。
    if hasattr(vcp, "update_calendar_event_fields"):
        original_update_fields = vcp.update_calendar_event_fields

        def update_calendar_event_fields(
            vcs_arg, calendar_row, confirmation="保持不變", color_choice="保持不變"
        ):
            event = (calendar_row or {}).get("event") or {}
            effective_monthly = confirmation == "每月確認" or (
                confirmation == "保持不變" and _is_monthly_event(event)
            )
            if effective_monthly:
                # vip_calendar_patch.py 使用舊命名，紫色／預排對應同一個 colorId。
                color_choice = "紫色／預排"
            return original_update_fields(
                vcs_arg, calendar_row,
                confirmation=confirmation,
                color_choice=color_choice,
            )

        vcp.update_calendar_event_fields = update_calendar_event_fields

    return vcs
