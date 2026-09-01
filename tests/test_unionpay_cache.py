import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import requests

import unionpay
from unionpay import JST, get_latest_two_rates


LATEST_RATE = {
    "source": "UnionPay International",
    "curDate": "2026-08-31",
    "rate": 0.048,
    "source_url": "https://example.test/20260831.json",
}

PREVIOUS_RATE = {
    "source": "UnionPay International",
    "curDate": "2026-08-30",
    "rate": 0.047,
    "source_url": "https://example.test/20260830.json",
}


class RateCacheTests(unittest.TestCase):
    def test_fetch_rate_uses_json_headers_accepted_by_unionpay(self):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "curDate": "2026-09-01",
                    "exchangeRateJson": [
                        {
                            "transCur": "JPY",
                            "baseCur": "CNY",
                            "rateData": "0.042223",
                        }
                    ],
                }

        def accepted_request(_url, *, timeout, headers=None):
            self.assertEqual(15, timeout)
            self.assertIn("UnionPayRateMonitor", headers["User-Agent"])
            self.assertIn("application/json", headers["Accept"])
            return FakeResponse()

        with patch("unionpay.requests.get", side_effect=accepted_request):
            rate = unionpay.fetch_rate_for_day(date(2026, 9, 1))

        self.assertEqual(0.042223, rate["rate"])

    def test_http_403_stops_refresh_after_first_attempt(self):
        class ForbiddenResponse:
            status_code = 403

            def raise_for_status(self):
                raise requests.HTTPError("403 Forbidden")

        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )
            (history_dir / "2026-08-31.json").write_text(
                json.dumps(LATEST_RATE), encoding="utf-8"
            )

            with patch("unionpay.requests.get", return_value=ForbiddenResponse()) as get:
                latest, previous = get_latest_two_rates(
                    data_dir=data_dir,
                    now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                )

            self.assertEqual(1, get.call_count)
            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

    def test_invalid_json_stops_refresh_after_first_attempt(self):
        class InvalidJsonResponse:
            status_code = 200

            def json(self):
                raise ValueError("not JSON")

        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )
            (history_dir / "2026-08-31.json").write_text(
                json.dumps(LATEST_RATE), encoding="utf-8"
            )

            with patch("unionpay.requests.get", return_value=InvalidJsonResponse()) as get:
                latest, previous = get_latest_two_rates(
                    data_dir=data_dir,
                    now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                )

            self.assertEqual(1, get.call_count)
            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

    def test_malformed_json_shape_falls_back_after_first_attempt(self):
        class MalformedResponse:
            status_code = 200

            def json(self):
                return []

        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )
            (history_dir / "2026-08-31.json").write_text(
                json.dumps(LATEST_RATE), encoding="utf-8"
            )

            with patch("unionpay.requests.get", return_value=MalformedResponse()) as get:
                latest, previous = get_latest_two_rates(
                    data_dir=data_dir,
                    now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                )

            self.assertEqual(1, get.call_count)
            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

    def test_snapshot_exposes_cache_refresh_time(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            refreshed_at = datetime(2026, 9, 1, 0, 15, tzinfo=JST)
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": refreshed_at.isoformat(),
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )

            latest, previous, updated_at = unionpay.get_latest_rate_snapshot(
                data_dir=data_dir,
                now=datetime(2026, 9, 1, 1, 0, tzinfo=JST),
                fetcher=lambda _day: self.fail("fresh cache must not be refreshed"),
            )

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)
            self.assertEqual(refreshed_at, updated_at)

    def test_fresh_cache_avoids_upstream_requests(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": "2026-08-31T06:00:00+09:00",
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )

            def unexpected_fetch(_day):
                self.fail("fresh cache must not request UnionPay")

            latest, previous = get_latest_two_rates(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 10, 0, tzinfo=JST),
                fetcher=unexpected_fetch,
            )

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

    def test_forced_refresh_bypasses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": "2026-08-31T11:00:00+09:00",
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )
            corrected_rate = {**LATEST_RATE, "rate": 0.049}
            rates_by_day = {
                "2026-08-31": corrected_rate,
                "2026-08-30": PREVIOUS_RATE,
            }

            latest, previous, updated_at = unionpay.get_latest_rate_snapshot(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=lambda day: rates_by_day.get(day.isoformat()),
                force_refresh=True,
            )

            self.assertEqual(corrected_rate, latest)
            self.assertEqual(PREVIOUS_RATE, previous)
            self.assertEqual(datetime(2026, 8, 31, 12, 0, tzinfo=JST), updated_at)

    def test_failed_manual_refresh_is_throttled_for_one_minute(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": "2026-08-31T01:00:00+09:00",
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )
            attempts = 0

            def unavailable_fetch(_day):
                nonlocal attempts
                attempts += 1
                raise requests.RequestException("UnionPay unavailable")

            first = unionpay.refresh_latest_rate_snapshot(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=unavailable_fetch,
            )
            second = unionpay.refresh_latest_rate_snapshot(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, 30, tzinfo=JST),
                fetcher=unavailable_fetch,
            )

            self.assertEqual(1, attempts)
            self.assertEqual("failed", first[3])
            self.assertEqual("throttled", second[3])

    def test_concurrent_manual_refreshes_make_one_upstream_attempt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": "2026-08-31T01:00:00+09:00",
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )
            attempts = 0
            attempts_lock = threading.Lock()

            def unavailable_fetch(_day):
                nonlocal attempts
                with attempts_lock:
                    attempts += 1
                time.sleep(0.05)
                raise requests.RequestException("UnionPay unavailable")

            def refresh():
                return unionpay.refresh_latest_rate_snapshot(
                    data_dir=data_dir,
                    now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                    fetcher=unavailable_fetch,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: refresh(), range(2)))

            self.assertEqual(1, attempts)
            self.assertEqual(["failed", "throttled"], sorted(result[3] for result in results))

    def test_refresh_persists_latest_cache_and_dated_history(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            rates_by_day = {
                "2026-08-31": LATEST_RATE,
                "2026-08-30": PREVIOUS_RATE,
            }

            latest, previous = get_latest_two_rates(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=lambda day: rates_by_day.get(day.isoformat()),
            )

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

            latest_payload = json.loads(
                (data_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual("2026-08-31T12:00:00+09:00", latest_payload["fetched_at"])
            self.assertEqual([LATEST_RATE, PREVIOUS_RATE], latest_payload["rates"])

            self.assertEqual(
                LATEST_RATE,
                json.loads(
                    (data_dir / "history" / "2026-08-31.json").read_text(
                        encoding="utf-8"
                    )
                ),
            )
            self.assertEqual(
                PREVIOUS_RATE,
                json.loads(
                    (data_dir / "history" / "2026-08-30.json").read_text(
                        encoding="utf-8"
                    )
                ),
            )

    def test_upstream_failure_falls_back_to_history(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )
            (history_dir / "2026-08-31.json").write_text(
                json.dumps(LATEST_RATE), encoding="utf-8"
            )

            latest, previous = get_latest_two_rates(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=lambda _day: None,
            )

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

    def test_connection_error_falls_back_after_first_attempt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )
            (history_dir / "2026-08-31.json").write_text(
                json.dumps(LATEST_RATE), encoding="utf-8"
            )
            attempts = 0

            def unavailable_fetch(_day):
                nonlocal attempts
                attempts += 1
                raise requests.RequestException("UnionPay unavailable")

            latest, previous = get_latest_two_rates(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=unavailable_fetch,
            )

            self.assertEqual(1, attempts)
            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

    def test_partial_refresh_keeps_previous_rate_from_history(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )

            latest, previous = get_latest_two_rates(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=lambda day: (
                    LATEST_RATE if day.isoformat() == "2026-08-31" else None
                ),
            )

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

            latest_payload = json.loads(
                (data_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual([LATEST_RATE, PREVIOUS_RATE], latest_payload["rates"])

    def test_refreshed_rate_takes_priority_over_stale_same_date_data(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            corrected_rate = {**LATEST_RATE, "rate": 0.049}
            (history_dir / "2026-08-30.json").write_text(
                json.dumps(PREVIOUS_RATE), encoding="utf-8"
            )
            (history_dir / "2026-08-31.json").write_text(
                json.dumps(LATEST_RATE), encoding="utf-8"
            )
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": "2026-08-30T01:00:00+09:00",
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )

            latest, previous = get_latest_two_rates(
                data_dir=data_dir,
                now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                fetcher=lambda day: (
                    corrected_rate if day.isoformat() == "2026-08-31" else None
                ),
            )

            self.assertEqual(corrected_rate, latest)
            self.assertEqual(PREVIOUS_RATE, previous)

            latest_payload = json.loads(
                (data_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(corrected_rate, latest_payload["rates"][0])

    def test_cache_write_failure_does_not_hide_fetched_rates(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory) / "read-only-data"
            data_dir.mkdir()
            os.chmod(data_dir, 0o500)
            rates_by_day = {
                "2026-08-31": LATEST_RATE,
                "2026-08-30": PREVIOUS_RATE,
            }

            try:
                with self.assertLogs("unionpay", level="WARNING") as captured_logs:
                    latest, previous, updated_at = unionpay.get_latest_rate_snapshot(
                        data_dir=data_dir,
                        now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                        fetcher=lambda day: rates_by_day.get(day.isoformat()),
                    )
            finally:
                os.chmod(data_dir, 0o700)

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)
            self.assertIsNone(updated_at)
            self.assertIn("Could not persist rate cache", captured_logs.output[0])

    def test_failed_refresh_keeps_previous_cache_time(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory) / "read-only-data"
            data_dir.mkdir()
            previous_refresh = datetime(2026, 8, 30, 1, 0, tzinfo=JST)
            corrected_rate = {**LATEST_RATE, "rate": 0.049}
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": previous_refresh.isoformat(),
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(data_dir, 0o500)
            rates_by_day = {
                "2026-08-31": corrected_rate,
                "2026-08-30": PREVIOUS_RATE,
            }

            try:
                with self.assertLogs("unionpay", level="WARNING"):
                    latest, previous, updated_at = unionpay.get_latest_rate_snapshot(
                        data_dir=data_dir,
                        now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                        fetcher=lambda day: rates_by_day.get(day.isoformat()),
                    )
            finally:
                os.chmod(data_dir, 0o700)

            self.assertEqual(corrected_rate, latest)
            self.assertEqual(PREVIOUS_RATE, previous)
            self.assertEqual(previous_refresh, updated_at)

    def test_history_enumeration_error_still_allows_stale_cache_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "fetched_at": "2026-08-30T01:00:00+09:00",
                        "rates": [LATEST_RATE, PREVIOUS_RATE],
                    }
                ),
                encoding="utf-8",
            )

            def failing_history_paths():
                raise OSError("history directory unavailable")
                yield

            with patch("unionpay.Path.glob", return_value=failing_history_paths()):
                latest, previous = get_latest_two_rates(
                    data_dir=data_dir,
                    now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                    fetcher=lambda _day: None,
                )

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)


if __name__ == "__main__":
    unittest.main()
