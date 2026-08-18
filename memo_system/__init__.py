# -*- coding: utf-8 -*-
"""memo_system package bootstrap.

This package is imported by ordersapp.py before the unified function menu is rendered.
We use that import point to add the two Lemon reserve actions to the existing menu
without duplicating the very large ordersapp.py entrypoint.
"""

from __future__ import annotations

import inspect
import re

import streamlit as st


_RESERVE_CREATE_LABEL = "檸檬保留單建單：分析期間人力、設定保留率並批次成立保留單。"
_RESERVE_CANCEL_LABEL = "檸檬保留單取消：依期間、時段與客人備註篩選後安全取消保留單。"


def _renumber_menu(options):
    """Insert reserve actions before section B and renumber normal menu rows."""
    rows = list(options or [])
    if any(_RESERVE_CREATE_LABEL in str(x) for x in rows):
        return rows

    insert_at = next(
        (i for i, value in enumerate(rows) if "B. 訂單附屬功能" in str(value)),
        len(rows),
    )
    rows[insert_at:insert_at] = [_RESERVE_CREATE_LABEL, _RESERVE_CANCEL_LABEL]

    counter = 0
    renumbered = []
    for value in rows:
        text = str(value)
        if text.strip().startswith("──"):
            renumbered.append(text)
            continue
        counter += 1
        text = re.sub(r"^\s*\d+\.\s*", "", text)
        renumbered.append(f"{counter}. {text}")
    return renumbered


def _install_orders_menu_patch():
    if getattr(st, "_lemon_reserve_menu_patch_installed", False):
        return

    original_selectbox = st.selectbox

    def patched_selectbox(label, options, *args, **kwargs):
        is_main_menu = (
            str(label) == "功能選單"
            and kwargs.get("key") == "unified_function_select"
        )
        if not is_main_menu:
            return original_selectbox(label, options, *args, **kwargs)

        menu_options = _renumber_menu(options)
        selected = original_selectbox(label, menu_options, *args, **kwargs)
        selected_text = re.sub(r"^\s*\d+\.\s*", "", str(selected))

        if selected_text.startswith("檸檬保留單建單：") or selected_text.startswith("檸檬保留單取消："):
            caller = inspect.currentframe().f_back
            app_globals = caller.f_globals if caller is not None else {}
            backend_email = str(app_globals.get("backend_email") or "")
            backend_password = str(app_globals.get("backend_password") or "")
            env = str(app_globals.get("env") or "")

            st.markdown("<hr>", unsafe_allow_html=True)
            from reserve_menu import render_reserve_cancel, render_reserve_create

            if selected_text.startswith("檸檬保留單建單："):
                render_reserve_create(backend_email, backend_password, env)
            else:
                render_reserve_cancel(backend_email, backend_password, env)
            st.stop()

        # Existing ordersapp.py expects the selected label to exist in its original
        # _menu_display_options list. Since we renumbered rows after adding two entries,
        # map an existing choice back to the original text by matching its description.
        description = re.sub(r"^\s*\d+\.\s*", "", str(selected))
        for original in list(options or []):
            original_description = re.sub(r"^\s*\d+\.\s*", "", str(original))
            if original_description == description:
                return original
        return selected

    st.selectbox = patched_selectbox
    st._lemon_reserve_menu_patch_installed = True


_install_orders_menu_patch()
