import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
BASE_URL = "https://www.unionpayintl.com/upload/jfimg/{}.json"
CACHE_TTL = timedelta(hours=6)
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
logger = logging.getLogger(__name__)


def fetch_rate_for_day(day):
    date_str = day.strftime("%Y%m%d")
    url = BASE_URL.format(date_str)

    try:
        response = requests.get(url, timeout=15)
    except requests.RequestException:
        raise

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


def _rate_date(rate):
    raw_date = str(rate.get("curDate", "")).strip()
    compact_date = raw_date.replace("-", "").replace("/", "")

    if len(compact_date) != 8 or not compact_date.isdigit():
        return None

    try:
        return datetime.strptime(compact_date, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _valid_rate(rate):
    if not isinstance(rate, dict) or _rate_date(rate) is None:
        return False

    try:
        return float(rate["rate"]) > 0
    except (KeyError, TypeError, ValueError):
        return False


def _read_json(path):
    try:
        with path.open(encoding="utf-8") as source_file:
            return json.load(source_file)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _read_latest_cache(data_dir):
    payload = _read_json(data_dir / "latest.json")

    if not isinstance(payload, dict):
        return None, []

    try:
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
    except (KeyError, TypeError, ValueError):
        fetched_at = None

    if fetched_at is not None and fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=JST)

    rates = payload.get("rates", [])
    if not isinstance(rates, list):
        return fetched_at, []

    return fetched_at, [dict(rate) for rate in rates if _valid_rate(rate)]


def _read_history(data_dir):
    history_dir = data_dir / "history"
    rates_by_date = {}

    try:
        history_paths = list(history_dir.glob("*.json"))
    except OSError:
        return []

    for history_path in history_paths:
        rate = _read_json(history_path)
        if _valid_rate(rate):
            rates_by_date[_rate_date(rate)] = dict(rate)

    return [rates_by_date[date_key] for date_key in sorted(rates_by_date, reverse=True)]


def _merge_rates(*rate_groups):
    rates_by_date = {}

    for rate_group in rate_groups:
        for rate in rate_group:
            if _valid_rate(rate):
                rates_by_date.setdefault(_rate_date(rate), dict(rate))

    return [rates_by_date[date_key] for date_key in sorted(rates_by_date, reverse=True)]


def _persist_rates(data_dir, rates, fetched_at):
    history_dir = data_dir / "history"

    for rate in rates:
        _atomic_write_json(history_dir / f"{_rate_date(rate)}.json", rate)

    _atomic_write_json(
        data_dir / "latest.json",
        {
            "fetched_at": fetched_at.isoformat(),
            "rates": rates,
        },
    )


def _as_pair(rates):
    latest = rates[0]
    previous = rates[1] if len(rates) > 1 else None
    return latest, previous


def _as_snapshot(rates, updated_at):
    latest, previous = _as_pair(rates)
    return latest, previous, updated_at


def get_latest_rate_snapshot(*, data_dir=None, now=None, fetcher=None):
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)

    fetcher = fetcher or fetch_rate_for_day
    fetched_at, cached_rates = _read_latest_cache(data_dir)

    if fetched_at is not None and cached_rates:
        cache_age = now - fetched_at.astimezone(now.tzinfo)
        if timedelta(0) <= cache_age <= CACHE_TTL:
            return _as_snapshot(cached_rates, fetched_at)

    today = now.astimezone(JST).date()
    found = []

    for offset in range(14):
        try:
            result = fetcher(today - timedelta(days=offset))
        except requests.RequestException:
            break

        if not _valid_rate(result):
            continue

        if any(_rate_date(rate) == _rate_date(result) for rate in found):
            continue

        found.append(dict(result))

        if len(found) == 2:
            break

    if found:
        refreshed_rates = _merge_rates(found, cached_rates, _read_history(data_dir))[:2]
        try:
            _persist_rates(data_dir, refreshed_rates, now)
        except OSError as error:
            logger.warning("Could not persist rate cache in %s: %s", data_dir, error)
        return _as_snapshot(refreshed_rates, now)

    fallback_rates = _merge_rates(cached_rates, _read_history(data_dir))
    if fallback_rates:
        return _as_snapshot(fallback_rates, fetched_at)

    raise RuntimeError("No UnionPay rate found")


def get_latest_two_rates(*, data_dir=None, now=None, fetcher=None):
    latest, previous, _updated_at = get_latest_rate_snapshot(
        data_dir=data_dir,
        now=now,
        fetcher=fetcher,
    )
    return latest, previous
