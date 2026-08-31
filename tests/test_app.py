import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import Request

import app
from unionpay import JST


LATEST_RATE = {
    "source": "UnionPay International",
    "curDate": "2026-08-31",
    "rate": 0.042255,
    "source_url": "https://example.test/20260831.json",
}

PREVIOUS_RATE = {
    "source": "UnionPay International",
    "curDate": "2026-08-30",
    "rate": 0.042334,
    "source_url": "https://example.test/20260830.json",
}


def make_request():
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


class HomePageTests(unittest.TestCase):
    def test_footer_shows_data_refresh_time_and_version(self):
        updated_at = datetime(2026, 9, 1, 0, 15, tzinfo=JST)

        with patch.object(
            app,
            "get_latest_two_rates",
            return_value=(LATEST_RATE, PREVIOUS_RATE),
            create=True,
        ), patch.object(
            app,
            "get_latest_rate_snapshot",
            return_value=(LATEST_RATE, PREVIOUS_RATE, updated_at),
            create=True,
        ):
            response = app.home(make_request())

        html = response.body.decode("utf-8")
        self.assertIn(
            "更新时间：2026-09-01 00:15 JST · v1.1.0",
            html,
        )


if __name__ == "__main__":
    unittest.main()
