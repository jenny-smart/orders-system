# -*- coding: utf-8 -*-
import streamlit as st
import vip_calendar_sync as vcs
from vip_calendar_patch import apply_patch

# Test the newer VIP calendar behavior without touching ordersapp.py yet.
# The patch makes a newly-created backend order reuse a nearby purple
# <每月確認/自行預約> calendar event when possible, instead of duplicating it.
apply_patch(vcs)

st.set_page_config(page_title="VIP 訂單／Google 日曆同步測試", layout="wide")
st.title("VIP 訂單／Google 日曆同步測試")

col1, col2, col3 = st.columns([3.2, 3.2, 1.2])
with col1:
    backend_email = st.text_input("後台帳號")
with col2:
    backend_password = st.text_input("後台密碼", type="password")
with col3:
    env_label = st.selectbox("環境", ["prod（正式機 backend）", "dev（測試機 backend-dev）"], index=1)
    env = "dev" if env_label.startswith("dev") else "prod"

st.divider()
vcs.render_vip_calendar_sync(backend_email.strip(), backend_password.strip(), env)
