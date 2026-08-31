import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import requests

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
                    latest, previous = get_latest_two_rates(
                        data_dir=data_dir,
                        now=datetime(2026, 8, 31, 12, 0, tzinfo=JST),
                        fetcher=lambda day: rates_by_day.get(day.isoformat()),
                    )
            finally:
                os.chmod(data_dir, 0o700)

            self.assertEqual(LATEST_RATE, latest)
            self.assertEqual(PREVIOUS_RATE, previous)
            self.assertIn("Could not persist rate cache", captured_logs.output[0])

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
