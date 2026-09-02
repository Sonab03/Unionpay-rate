import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

import unionpay


RATE_2026_08_28 = {
    "source": "UnionPay International",
    "curDate": "2026-08-28",
    "rate": 0.042334,
    "source_url": "https://example.test/20260828.json",
}


class HistoricalRateTests(unittest.TestCase):
    def test_exact_cached_day_avoids_upstream(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-28.json").write_text(
                json.dumps(RATE_2026_08_28), encoding="utf-8"
            )

            result = unionpay.get_rate_for_date(
                date(2026, 8, 28),
                data_dir=data_dir,
                fetcher=lambda _day: self.fail("cached rate must not fetch"),
            )

            self.assertEqual("2026-08-28", result["requestedDate"])
            self.assertEqual("2026-08-28", result["rateDate"])
            self.assertEqual(0.042334, result["rate"])

    def test_missing_days_fall_back_to_nearest_previous_rate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            seen = []

            def fetcher(day):
                seen.append(day.isoformat())
                return RATE_2026_08_28 if day == date(2026, 8, 28) else None

            result = unionpay.get_rate_for_date(
                date(2026, 8, 30), data_dir=data_dir, fetcher=fetcher
            )

            self.assertEqual(
                ["2026-08-30", "2026-08-29", "2026-08-28"], seen
            )
            self.assertEqual("2026-08-30", result["requestedDate"])
            self.assertEqual("2026-08-28", result["rateDate"])
            self.assertTrue((data_dir / "history" / "2026-08-28.json").exists())
            self.assertTrue(
                (data_dir / "date-lookups" / "2026-08-30.json").exists()
            )

    def test_saved_date_mapping_avoids_repeating_missing_day_requests(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)

            def first_fetch(day):
                return RATE_2026_08_28 if day == date(2026, 8, 28) else None

            first = unionpay.get_rate_for_date(
                date(2026, 8, 30), data_dir=data_dir, fetcher=first_fetch
            )
            second = unionpay.get_rate_for_date(
                date(2026, 8, 30),
                data_dir=data_dir,
                fetcher=lambda _day: self.fail("saved lookup must not fetch"),
            )

            self.assertEqual(first, second)

    def test_upstream_error_stops_without_using_older_cached_rate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            history_dir = data_dir / "history"
            history_dir.mkdir()
            (history_dir / "2026-08-28.json").write_text(
                json.dumps(RATE_2026_08_28), encoding="utf-8"
            )
            attempts = 0

            def unavailable(_day):
                nonlocal attempts
                attempts += 1
                raise requests.RequestException("upstream unavailable")

            with self.assertRaises(requests.RequestException):
                unionpay.get_rate_for_date(
                    date(2026, 8, 30), data_dir=data_dir, fetcher=unavailable
                )

            self.assertEqual(1, attempts)

    def test_lookup_stops_after_fourteen_days(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            attempts = []

            result = unionpay.get_rate_for_date(
                date(2026, 8, 30),
                data_dir=Path(temporary_directory),
                fetcher=lambda day: attempts.append(day) or None,
            )

            self.assertIsNone(result)
            self.assertEqual(14, len(attempts))
            self.assertEqual(date(2026, 8, 17), attempts[-1])

    def test_concurrent_lookups_share_one_upstream_round(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            attempts = 0
            attempts_lock = threading.Lock()

            def fetcher(day):
                nonlocal attempts
                with attempts_lock:
                    attempts += 1
                time.sleep(0.03)
                return RATE_2026_08_28 if day == date(2026, 8, 28) else None

            def lookup():
                return unionpay.get_rate_for_date(
                    date(2026, 8, 30), data_dir=data_dir, fetcher=fetcher
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: lookup(), range(2)))

            self.assertEqual(3, attempts)
            self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
