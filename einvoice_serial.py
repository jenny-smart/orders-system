# einvoice_serial.py
# 財政部電子發票字軌號碼取號（半自動：畫面引導＋人工登入）
#
# 登入財政部電子發票平台（含圖形驗證碼）由客服自行在瀏覽器操作，本系統不經手、
# 不儲存密碼。系統負責：
#   1. 提示該區域要用哪組統一編號登入、以及目前該查詢哪個發票期別
#   2. 客服下載 CSV 後回來上傳，系統依規則重新命名
#   3. 自動存入 Google Drive 對應區域／期別資料夾（紙本發票／期別）
#   4. 寫入「Jenny's Lemonhometools」試算表的「執行紀錄」分頁
import io
from datetime import date, datetime

import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import orders
from env import (
    EINVOICE_LEMONHOMETOOLS_SHEET_ID,
    EINVOICE_LOGIN_URL,
    EINVOICE_REGION_UBN_MAP,
)

_PERIOD_LABELS = ["01-02", "03-04", "05-06", "07-08", "09-10", "11-12"]

_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _period_of(d: date):
    label = _PERIOD_LABELS[(d.month - 1) // 2]
    return d.year, label


def _next_period(year: int, label: str):
    idx = _PERIOD_LABELS.index(label)
    if idx == len(_PERIOD_LABELS) - 1:
        return year + 1, _PERIOD_LABELS[0]
    return year, _PERIOD_LABELS[idx + 1]


def _period_filename(year: int, label: str, region: str) -> str:
    start_mm, end_mm = label.split("-")
    return f"{year}{start_mm}{end_mm}電子發票取號-{region}.csv"


def _build_credentials():
    info = orders.get_service_account_info()
    return Credentials.from_service_account_info(info, scopes=_DRIVE_SCOPES)


def _find_worksheet_by_headers(sh, required_headers):
    for ws in sh.worksheets():
        row1 = ws.row_values(1)
        if all(h in row1 for h in required_headers):
            return ws
    return None


def _load_region_row(sh, region):
    ws = _find_worksheet_by_headers(sh, ["地區", "財務根目錄ID"])
    if ws is None:
        raise Exception("在「Jenny's Lemonhometools」試算表找不到含「地區／財務根目錄ID」欄位的分頁。")
    values = ws.get_all_values()
    headers = values[0]
    idx_region = headers.index("地區")
    for row in values[1:]:
        if len(row) > idx_region and row[idx_region].strip() == region:
            return dict(zip(headers, row))
    raise Exception(f"「地區」設定分頁裡找不到區域「{region}」，請先在試算表新增這個區域的設定列。")


def _find_or_create_child_folder(drive, parent_id, name):
    query = (
        f"'{parent_id}' in parents and name = '{name}' "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    resp = drive.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    folder = drive.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]


def _resolve_target_folder(drive, region_row, period_label):
    root_id = (region_row.get("財務根目錄ID") or "").strip()
    if not root_id:
        root_id = (region_row.get("承攬費總根目錄ID") or "").strip()
    if not root_id:
        raise Exception(
            f"「{region_row.get('地區')}」尚未設定財務根目錄ID／承攬費總根目錄ID，無法決定存檔位置。"
        )
    paper_folder_id = _find_or_create_child_folder(drive, root_id, "紙本發票")
    period_folder_id = _find_or_create_child_folder(drive, paper_folder_id, period_label)
    return period_folder_id


def _upload_csv_to_drive(drive, folder_id, filename, file_bytes):
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype="text/csv", resumable=False)
    return drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()


def _append_log_row(sh, values_by_header):
    ws = _find_worksheet_by_headers(sh, ["執行時間", "系統", "功能"])
    if ws is None:
        return False
    headers = ws.row_values(1)
    row = [values_by_header.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")
    return True


def render_einvoice_serial():
    regions = list(EINVOICE_REGION_UBN_MAP.keys())
    today = date.today()
    cur_year, cur_label = _period_of(today)
    target_year, target_label = _next_period(cur_year, cur_label)

    col_r, col_p = st.columns([1.4, 1.6])
    with col_r:
        region = st.selectbox("區域", regions, key="einvoice_region")
    with col_p:
        period_options = [f"{cur_year}年{cur_label}期", f"{target_year}年{target_label}期"]
        period_default = 1  # 預設抓「下一期」，符合平台於雙月18日配下期字軌的節奏
        period_choice = st.selectbox("要查詢／下載的發票期別", period_options, index=period_default, key="einvoice_period")

    if period_choice == period_options[0]:
        query_year, query_label = cur_year, cur_label
    else:
        query_year, query_label = target_year, target_label

    ubn = EINVOICE_REGION_UBN_MAP[region]
    target_filename = _period_filename(query_year, query_label, region)

    st.markdown(f"**這次要用的統一編號／帳號：`{ubn}`（{region}）**")

    st.markdown(
        f"""
1. 開啟〔[財政部電子發票整合服務平台登入頁]({EINVOICE_LOGIN_URL})〕，統一編號與帳號都輸入 `{ubn}`，密碼由客服自行輸入，並手動完成圖形驗證碼登入。
2. 左側選單點「電子發票字軌號碼取號」→「電子發票字軌號碼取號」。
3. 上方頁籤先按「查詢」。
4. 發票期別設定為 **{query_year}年{query_label}期**（若清單裡沒有這個期別，代表尚未取號，需先切到「取號」頁籤申請）。
5. 按下方「查詢」按鈕，勾選該筆資料後按「下載」，存到電腦。
6. 回到這裡，把剛下載的 CSV 上傳到下方，系統會自動重新命名為 `{target_filename}` 並存進 Google Drive「{region}」對應資料夾（紙本發票／{query_label}），找不到期別資料夾會自動新增。
        """
    )

    uploaded = st.file_uploader("上傳剛下載的電子發票取號 CSV", type=["csv"], key="einvoice_csv_upload")
    if uploaded is None:
        return

    st.caption(f"上傳後將另存為：{target_filename}")

    if not st.button("存入 Google Drive 並寫入執行紀錄", type="primary", key="einvoice_upload_btn"):
        return

    file_bytes = uploaded.getvalue()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        creds = _build_credentials()
        gc = orders.gspread.authorize(creds)
        sh = gc.open_by_key(EINVOICE_LEMONHOMETOOLS_SHEET_ID)
        region_row = _load_region_row(sh, region)

        drive = build("drive", "v3", credentials=creds)
        folder_id = _resolve_target_folder(drive, region_row, query_label)
        result = _upload_csv_to_drive(drive, folder_id, target_filename, file_bytes)

        logged = _append_log_row(sh, {
            "執行時間": now_str,
            "系統": "orders-system",
            "功能": "電子發票取號",
            "執行方式": "手動",
            "區域": region,
            "期別/日期": f"{query_year}年{query_label}期",
            "目標位置": result.get("webViewLink", folder_id),
            "來源檔名": uploaded.name,
            "結果": "成功",
            "訊息": f"已存為 {target_filename}",
        })
    except Exception as e:
        st.error(f"存檔失敗：{e}")
        st.info(
            "常見原因：服務帳戶尚未被加入「Jenny's Lemonhometools」試算表的編輯權限，"
            "或該區域的 Google Drive 資料夾尚未分享給服務帳戶。"
        )
        return

    st.success(f"已存入 Google Drive：{target_filename}")
    if result.get("webViewLink"):
        st.markdown(f"[開啟檔案]({result['webViewLink']})")
    if not logged:
        st.warning("檔案已上傳成功，但找不到「執行紀錄」分頁，這筆紀錄沒有寫入試算表。")
