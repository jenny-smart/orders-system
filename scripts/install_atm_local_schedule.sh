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
    "StartCalendarInterval": [
        {"Hour": 8, "Minute": 0},
        {"Hour": 15, "Minute": 0},
    ],
    "StandardOutPath": log_path,
    "StandardErrorPath": log_path,
    "ProcessType": "Background",
}
with open(plist_path, "wb") as handle:
    plistlib.dump(payload, handle)
PY

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"

echo "已安裝 ATM 本機排程：每日 08:00、15:00"
echo "帳密來源：$ACCOUNTS_FILE"
echo "執行記錄：$LOG_PATH"
