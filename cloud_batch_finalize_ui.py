# -*- coding: utf-8 -*-
from __future__ import annotations

import os

import requests
import streamlit as st

from batch_booking_optimized import _load_candidates

REPO = "jenny-smart/orders-system"
WORKFLOW = "batch-finalize-orders.yml"


def _github_token() -> str:
    for key in ("GITHUB_ACTIONS_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            return value
        try:
            if key in st.secrets:
                value = str(st.secrets[key]).strip()
                if value:
                    return value
        except Exception:
            pass
    return ""


def _dispatch(sheet_name: str, chunk_size: int, max_rows: int) -> None:
    token = _github_token()
    if not token:
        raise RuntimeError(
            "尚未設定 GitHub Actions Token。請在 Streamlit secrets 設定 GITHUB_ACTIONS_TOKEN，"
            "權限需可執行 orders-system 的 Actions workflow。"
        )

    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    response = requests.post(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "ref": "main",
            "inputs": {
                "sheet_name": sheet_name,
                "chunk_size": str(chunk_size),
                "max_rows": str(max_rows),
            },
        },
        timeout=30,
    )
    if response.status_code != 204:
        raise RuntimeError(f"GitHub workflow_dispatch 失敗：{response.status_code} {response.text[:500]}")


def render() -> None:
    st.subheader("批次建單優化＋雲端批次成單")
    st.info(
        "保留原『批次建單優化』供人工成單；此功能使用同一套優化核心，"
        "交由 GitHub Actions 在雲端持續執行，電腦關機後仍會繼續。"
    )

    sheet_name = st.text_input(
        "工作表名稱",
        placeholder="例如：台北202609、台中202609",
        key="cloud_batch_finalize_sheet",
    ).strip()

    c1, c2 = st.columns(2)
    with c1:
        chunk_size = st.number_input(
            "每輪最多處理列數",
            min_value=1,
            max_value=200,
            value=50,
            step=10,
            key="cloud_batch_finalize_chunk",
        )
    with c2:
        max_rows = st.number_input(
            "本次最多處理列數（0＝全部）",
            min_value=0,
            max_value=5000,
            value=0,
            step=10,
            key="cloud_batch_finalize_max_rows",
        )

    pending_count = None
    if sheet_name and st.button("檢查待成單筆數", key="cloud_batch_finalize_check"):
        try:
            pending_count = len(_load_candidates(sheet_name))
            st.session_state.cloud_batch_finalize_pending = pending_count
        except Exception as exc:
            st.error(f"讀取工作表失敗：{exc}")

    if pending_count is None:
        pending_count = st.session_state.get("cloud_batch_finalize_pending")
    if pending_count is not None:
        st.metric("目前待成單", f"{pending_count} 列")

    confirm = st.checkbox(
        "我確認要在正式機以批次建單優化方式執行建單、寄確認信、同步 Google 日曆",
        key="cloud_batch_finalize_confirm",
    )
    if st.button(
        "開始雲端批次成單",
        type="primary",
        disabled=not sheet_name or not confirm,
        key="cloud_batch_finalize_start",
    ):
        try:
            _dispatch(sheet_name, int(chunk_size), int(max_rows))
            st.success("已啟動雲端批次成單。關閉瀏覽器或電腦不會中斷 GitHub Actions。")
            st.markdown(f"[查看 GitHub Actions 執行進度](https://github.com/{REPO}/actions/workflows/{WORKFLOW})")
        except Exception as exc:
            st.error(str(exc))
