# env.py
ENV = "dev"
BASE_URL_DEV = "https://backend-dev.lemonclean.com.tw"
BASE_URL_PROD = "https://backend.lemonclean.com.tw"
ORDER_PREFIX_DEV = "TT"
ORDER_PREFIX_PROD = "LC"
GOOGLE_SHEET_ID = "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE"
ENABLE_GCAL_COLOR_SYNC = True
GOOGLE_SERVICE_ACCOUNT_FILE = "google_service_account.json"
GOOGLE_CALENDAR_MAP = {
    "台北": "lemonclean925@gmail.com",
    "台中": "lemonclean.com.tw_8soa7prkegf34tjp5b3okl6rqs@group.calendar.google.com",
}
COLOR_PURPLE = "3"
COLOR_YELLOW = "5"
REQUEST_DELAY = 1
GOOGLE_MAPS_API_KEY = "AIzaSyCvWQpentAJEdAhVxj7tvcMFcMZagCZcPg"  # 請填入有開通 Geocoding API 的金鑰

# 財政部電子發票字軌號碼取號（半自動：畫面引導＋人工登入，見 einvoice_serial.py）
EINVOICE_LOGIN_URL = "https://www.einvoice.nat.gov.tw/accounts/login/b"
# 區域 -> 財政部電子發票平台統一編號（帳號欄位與統一編號相同）；密碼由客服自行輸入，系統不儲存密碼。
EINVOICE_REGION_UBN_MAP = {
    "台北": "42627791",
    "台中": "82830399",
}
# 「Jenny's Lemonhometools」試算表：內含各區域 Google Drive 財務根目錄／承攬費總根目錄對照，
# 以及跨系統共用的「執行紀錄」分頁，取號完成後會寫入一筆紀錄。
EINVOICE_LEMONHOMETOOLS_SHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
