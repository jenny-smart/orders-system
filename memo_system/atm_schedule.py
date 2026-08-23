# -*- coding: utf-8 -*-
"""GitHub Actions：每小時同步台北／台中 ATM 待付款清單。"""
import os

from . import atm, memo


REGIONS = {
    "台北": ("ATM_TAIPEI_EMAIL", "ATM_TAIPEI_PASSWORD"),
    "台中": ("ATM_TAICHUNG_EMAIL", "ATM_TAICHUNG_PASSWORD"),
}


def main() -> None:
    errors = []
    for region, (email_key, password_key) in REGIONS.items():
        try:
            email = os.getenv(email_key, "").strip()
            password = os.getenv(password_key, "").strip()
            if not email or not password:
                raise RuntimeError(f"缺少 {email_key} 或 {password_key}")

            memo.set_runtime_credentials(email, password)
            session = memo.login()
            rows = atm.search_atm_unpaid_orders(session=session)
            result = atm.paste_atm_unpaid_list(region=region, rows=rows)
            print(
                f"{region}：新增 {result['pasted']} 筆，"
                f"略過重複 {result.get('skipped_duplicates', 0)} 筆"
            )
        except Exception as exc:
            errors.append(f"{region}：{exc}")
            print(f"❌ {region}：{exc}")

    if errors:
        raise RuntimeError("；".join(errors))


if __name__ == "__main__":
    main()
