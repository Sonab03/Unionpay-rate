import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
BASE_URL = "https://www.unionpayintl.com/upload/jfimg/{}.json"
CACHE_TTL = timedelta(hours=6)
MANUAL_REFRESH_COOLDOWN = timedelta(minutes=1)
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
MANUAL_REFRESH_ATTEMPT_FILE = ".manual-refresh-attempt.json"
MANUAL_REFRESH_LOCK_FILE = ".manual-refresh.lock"
HISTORICAL_LOOKUP_LOCK_FILE = ".historical-rate.lock"
DATE_LOOKUP_DIR = "date-lookups"
HISTORICAL_LOOKBACK_DAYS = 14
UPSTREAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; UnionPayRateMonitor/1.1; "
        "+https://rate.sonab.uk)"
    ),
    "Accept": "application/json,text/plain,*/*",
}
logger = logging.getLogger(__name__)


def fetch_rate_for_day(day):
    date_str = day.strftime("%Y%m%d")
    url = BASE_URL.format(date_str)

    try:
        response = requests.get(url, timeout=15, headers=UPSTREAM_HEADERS)
    except requests.RequestException:
        raise

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        response.raise_for_status()
        raise requests.RequestException(
            f"Unexpected UnionPay response: HTTP {response.status_code}"
        )

    try:
        data = response.json()
    except ValueError as error:
        raise requests.RequestException("UnionPay returned invalid JSON") from error

    if not isinstance(data, dict):
        raise requests.RequestException("UnionPay returned an invalid response shape")

    exchange_rates = data.get("exchangeRateJson")
    if not isinstance(exchange_rates, list):
        raise requests.RequestException("UnionPay returned an invalid rate list")

    for item in exchange_rates:
        if not isinstance(item, dict):
            raise requests.RequestException("UnionPay returned an invalid rate entry")

        if item.get("transCur") == "JPY" and item.get("baseCur") == "CNY":
            try:
                rate = float(item["rateData"])
            except (KeyError, TypeError, ValueError) as error:
                raise requests.RequestException(
                    "UnionPay returned an invalid JPY/CNY rate"
                ) from error

            result = {
                "source": "UnionPay International",
                "curDate": data.get("curDate"),
                "rate": rate,
                "source_url": url,
            }
            if not _valid_rate(result):
                raise requests.RequestException(
                    "UnionPay returned invalid JPY/CNY rate data"
                )
            return result

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


@contextmanager
def _historical_rate_lock(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / HISTORICAL_LOOKUP_LOCK_FILE).open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_history_rate(data_dir, day):
    rate = _read_json(data_dir / "history" / f"{day.isoformat()}.json")
    return dict(rate) if _valid_rate(rate) else None


def _historical_response(requested_day, rate):
    return {
        "requestedDate": requested_day.isoformat(),
        "rateDate": _rate_date(rate),
        "rate": float(rate["rate"]),
        "source": rate.get("source", "UnionPay International"),
    }


def _read_date_lookup(data_dir, requested_day):
    result = _read_json(
        data_dir / DATE_LOOKUP_DIR / f"{requested_day.isoformat()}.json"
    )
    if not isinstance(result, dict):
        return None
    if result.get("requestedDate") != requested_day.isoformat():
        return None
    try:
        rate_day = datetime.strptime(result["rateDate"], "%Y-%m-%d").date()
        rate = float(result["rate"])
    except (KeyError, TypeError, ValueError):
        return None
    if rate <= 0 or rate_day > requested_day:
        return None
    if (requested_day - rate_day).days >= HISTORICAL_LOOKBACK_DAYS:
        return None
    return {
        "requestedDate": requested_day.isoformat(),
        "rateDate": rate_day.isoformat(),
        "rate": rate,
        "source": str(result.get("source", "UnionPay International")),
    }


def _persist_history_rate(data_dir, rate):
    _atomic_write_json(data_dir / "history" / f"{_rate_date(rate)}.json", rate)


def _persist_date_lookup(data_dir, requested_day, result):
    _atomic_write_json(
        data_dir / DATE_LOOKUP_DIR / f"{requested_day.isoformat()}.json",
        result,
    )


def get_rate_for_date(requested_day, *, data_dir=None, fetcher=None):
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    fetcher = fetcher or fetch_rate_for_day

    with _historical_rate_lock(data_dir):
        mapped = _read_date_lookup(data_dir, requested_day)
        if mapped is not None:
            return mapped

        for offset in range(HISTORICAL_LOOKBACK_DAYS):
            candidate_day = requested_day - timedelta(days=offset)
            rate = _read_history_rate(data_dir, candidate_day)
            if rate is None:
                rate = fetcher(candidate_day)
                if _valid_rate(rate):
                    _persist_history_rate(data_dir, rate)

            if _valid_rate(rate):
                result = _historical_response(requested_day, rate)
                _persist_date_lookup(data_dir, requested_day, result)
                return result

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


@contextmanager
def _manual_refresh_lock(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / MANUAL_REFRESH_LOCK_FILE).open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


def _stored_snapshot(data_dir):
    fetched_at, cached_rates = _read_latest_cache(data_dir)
    fallback_rates = _merge_rates(cached_rates, _read_history(data_dir))
    if not fallback_rates:
        raise RuntimeError("No UnionPay rate found")
    return _as_snapshot(fallback_rates, fetched_at)


def _read_manual_refresh_attempt(data_dir):
    payload = _read_json(data_dir / MANUAL_REFRESH_ATTEMPT_FILE)
    if not isinstance(payload, dict):
        return None
    try:
        attempted_at = datetime.fromisoformat(payload["attempted_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=JST)
    return attempted_at


def _normalized_now(now):
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    return now


def get_latest_rate_snapshot(
    *, data_dir=None, now=None, fetcher=None, force_refresh=False
):
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    now = _normalized_now(now)

    fetcher = fetcher or fetch_rate_for_day
    fetched_at, cached_rates = _read_latest_cache(data_dir)

    if not force_refresh and fetched_at is not None and cached_rates:
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
        updated_at = fetched_at
        try:
            _persist_rates(data_dir, refreshed_rates, now)
        except OSError as error:
            logger.warning("Could not persist rate cache in %s: %s", data_dir, error)
        else:
            updated_at = now
        return _as_snapshot(refreshed_rates, updated_at)

    fallback_rates = _merge_rates(cached_rates, _read_history(data_dir))
    if fallback_rates:
        return _as_snapshot(fallback_rates, fetched_at)

    raise RuntimeError("No UnionPay rate found")


def refresh_latest_rate_snapshot(*, data_dir=None, now=None, fetcher=None):
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    now = _normalized_now(now)

    try:
        with _manual_refresh_lock(data_dir):
            fetched_before, cached_rates = _read_latest_cache(data_dir)

            if fetched_before is not None and cached_rates:
                cache_age = now - fetched_before.astimezone(now.tzinfo)
                if timedelta(0) <= cache_age <= MANUAL_REFRESH_COOLDOWN:
                    return (*_as_snapshot(cached_rates, fetched_before), "current")

            attempted_at = _read_manual_refresh_attempt(data_dir)
            if attempted_at is not None:
                attempt_age = now - attempted_at.astimezone(now.tzinfo)
                if timedelta(0) <= attempt_age <= MANUAL_REFRESH_COOLDOWN:
                    return (*_stored_snapshot(data_dir), "throttled")

            try:
                _atomic_write_json(
                    data_dir / MANUAL_REFRESH_ATTEMPT_FILE,
                    {"attempted_at": now.isoformat()},
                )
            except OSError as error:
                logger.warning(
                    "Could not record manual refresh attempt in %s: %s",
                    data_dir,
                    error,
                )
                return (*_stored_snapshot(data_dir), "failed")

            latest, previous, updated_at = get_latest_rate_snapshot(
                data_dir=data_dir,
                now=now,
                fetcher=fetcher,
                force_refresh=True,
            )
            refreshed = updated_at is not None and (
                fetched_before is None or updated_at > fetched_before
            )
            status = "updated" if refreshed else "failed"
            return latest, previous, updated_at, status
    except OSError as error:
        logger.warning("Could not lock manual refresh in %s: %s", data_dir, error)
        return (*_stored_snapshot(data_dir), "failed")


def get_latest_two_rates(*, data_dir=None, now=None, fetcher=None):
    latest, previous, _updated_at = get_latest_rate_snapshot(
        data_dir=data_dir,
        now=now,
        fetcher=fetcher,
    )
    return latest, previous
