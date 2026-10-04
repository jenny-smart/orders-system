#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
ACCOUNTS_FILE="${LEMON_ACCOUNTS_FILE:-$HOME/lemon/accounts.py}"
LABEL="com.lemonclean.orders-system.atm-unpaid"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_PATH="$HOME/Library/Logs/lemonclean-atm-unpaid.log"

if [ ! -f "$ACCOUNTS_FILE" ]; then
  echo "找不到本機帳密：$ACCOUNTS_FILE"
  exit 1
fi

(
  cd "$REPO_DIR"
  "$PYTHON_BIN" -c "import gspread, requests; from accounts import ACCOUNTS; assert ACCOUNTS['台北']['email'] and ACCOUNTS['台北']['password']; assert ACCOUNTS['台中']['email'] and ACCOUNTS['台中']['password']"
)

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

"$PYTHON_BIN" - "$PLIST_PATH" "$REPO_DIR" "$PYTHON_BIN" "$ACCOUNTS_FILE" "$LOG_PATH" <<'PY'
import plistlib
import sys

plist_path, repo_dir, python_bin, accounts_file, log_path = sys.argv[1:]
payload = {
    "Label": "com.lemonclean.orders-system.atm-unpaid",
    "ProgramArguments": [
        python_bin,
        "-m",
        "memo_system.atm",
        "--scheduled-unpaid",
    ],
    "WorkingDirectory": repo_dir,
    "EnvironmentVariables": {
        "LEMON_ACCOUNTS_FILE": accounts_file,
    },
    "StartCalendarInterval": [],
    "StandardOutPath": log_path,
    "StandardErrorPath": log_path,
    "ProcessType": "Background",
}
import os
os.chdir(repo_dir)
sys.path.insert(0, repo_dir)
from memo_system.atm_schedule import load_config, scheduled_slots
config, calendar = load_config()
from datetime import datetime
from zoneinfo import ZoneInfo
assert datetime.now(ZoneInfo(config["timezone"])).date().isoformat() in calendar["days"], "請先更新辦公日曆"
payload["StartCalendarInterval"] = [
    {"Hour": slot // 60, "Minute": slot % 60}
    for slot in scheduled_slots(config)
]
if os.getenv("ATM_SCHEDULE_CONFIG"):
    payload["EnvironmentVariables"]["ATM_SCHEDULE_CONFIG"] = os.environ["ATM_SCHEDULE_CONFIG"]
with open(plist_path, "wb") as handle:
    plistlib.dump(payload, handle)
PY

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"

echo "已安裝 ATM 本機排程：依設定檔執行，週末／國定假日／補假自動略過"
echo "帳密來源：$ACCOUNTS_FILE"
echo "執行記錄：$LOG_PATH"
