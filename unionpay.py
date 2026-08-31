from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
BASE_URL = "https://www.unionpayintl.com/upload/jfimg/{}.json"


def fetch_rate_for_day(day):
    date_str = day.strftime("%Y%m%d")
    url = BASE_URL.format(date_str)

    try:
        response = requests.get(url, timeout=15)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    for item in data.get("exchangeRateJson", []):
        if item.get("transCur") == "JPY" and item.get("baseCur") == "CNY":
            return {
                "source": "UnionPay International",
                "curDate": data.get("curDate"),
                "rate": float(item["rateData"]),
                "source_url": url,
            }

    return None


def get_latest_two_rates():
    today = datetime.now(JST).date()
    found = []

    for offset in range(14):
        result = fetch_rate_for_day(today - timedelta(days=offset))

        if result is None:
            continue

        if any(x["curDate"] == result["curDate"] for x in found):
            continue

        found.append(result)

        if len(found) == 2:
            break

    if not found:
        raise RuntimeError("No UnionPay rate found")

    latest = found[0]
    previous = found[1] if len(found) > 1 else None

    return latest, previous