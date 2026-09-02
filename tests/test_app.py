import unittest
from datetime import datetime
from unittest.mock import patch

import requests
from fastapi import HTTPException, Request

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


def make_request(path="/", method="GET", query_string=b""):
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query_string,
            "root_path": "",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


class HomePageTests(unittest.TestCase):
    def test_expenses_page_renders_tracker_shell(self):
        response = app.expenses(make_request(path="/expenses"))

        html = response.body.decode("utf-8")
        self.assertIn("日元消费汇总", html)
        self.assertIn('id="expenseForm"', html)
        self.assertIn('src="/static/expense_tracker_core.js"', html)
        self.assertIn('src="/static/expenses.js"', html)

    def test_rate_api_returns_requested_and_actual_dates(self):
        historical_rate = {
            "requestedDate": "2026-08-30",
            "rateDate": "2026-08-28",
            "rate": 0.042334,
            "source": "UnionPay International",
        }

        with patch.object(
            app, "get_rate_for_date", return_value=historical_rate, create=True
        ):
            result = app.rate_for_date("2026-08-30")

        self.assertEqual(historical_rate, result)

    def test_rate_api_rejects_invalid_and_future_dates(self):
        for raw_date in ("2026-02-30", "30-08-2026", "2999-01-01"):
            with self.subTest(raw_date=raw_date):
                with self.assertRaises(HTTPException) as captured:
                    app.rate_for_date(raw_date)
                self.assertEqual(400, captured.exception.status_code)

    def test_rate_api_returns_404_when_lookback_has_no_rate(self):
        with patch.object(app, "get_rate_for_date", return_value=None, create=True):
            with self.assertRaises(HTTPException) as captured:
                app.rate_for_date("2026-08-30")

        self.assertEqual(404, captured.exception.status_code)

    def test_rate_api_returns_502_on_upstream_error(self):
        with patch.object(
            app,
            "get_rate_for_date",
            side_effect=requests.RequestException("unavailable"),
            create=True,
        ):
            with self.assertRaises(HTTPException) as captured:
                app.rate_for_date("2026-08-30")

        self.assertEqual(502, captured.exception.status_code)

    def test_home_includes_system_aware_theme_control(self):
        updated_at = datetime(2026, 9, 1, 0, 15, tzinfo=JST)

        with patch.object(
            app,
            "get_latest_rate_snapshot",
            return_value=(LATEST_RATE, PREVIOUS_RATE, updated_at),
        ):
            response = app.home(make_request())

        html = response.body.decode("utf-8")
        self.assertIn('id="themeToggle"', html)
        self.assertIn('aria-label="切换颜色主题"', html)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn('data-theme="dark"', html)
        self.assertIn('data-theme="light"', html)
        self.assertIn('localStorage.getItem("theme")', html)
        self.assertIn('localStorage.setItem("theme", nextTheme)', html)
        self.assertIn("root.removeAttribute(\"data-theme\")", html)

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
            "数据更新时间：2026-09-01 00:15 JST",
            html,
        )
        self.assertIn(
            "@Sonab · v1.1.4",
            html,
        )

    def test_primary_rate_uses_subtle_jpy_line_and_centered_cny_line(self):
        updated_at = datetime(2026, 9, 1, 0, 15, tzinfo=JST)

        with patch.object(
            app,
            "get_latest_rate_snapshot",
            return_value=(LATEST_RATE, PREVIOUS_RATE, updated_at),
        ):
            response = app.home(make_request())

        html = response.body.decode("utf-8")
        self.assertIn('class="rate-jpy"', html)
        self.assertIn("10,000 JPY =", html)
        self.assertIn('class="rate-cny"', html)
        self.assertIn("422.550 CNY", html)
        self.assertRegex(
            html,
            r"(?s)\.rate-jpy\s*\{[^}]*color: var\(--subtle\);"
            r"[^}]*font-size: 16px;"
            r"[^}]*text-align: left;",
        )
        self.assertRegex(
            html,
            r"(?s)\.rate-cny\s*\{[^}]*font-size: 32px;"
            r"[^}]*text-align: center;",
        )

    def test_home_shows_manual_refresh_button_and_success_message(self):
        updated_at = datetime(2026, 9, 1, 0, 15, tzinfo=JST)

        with patch.object(
            app,
            "get_latest_rate_snapshot",
            return_value=(LATEST_RATE, PREVIOUS_RATE, updated_at),
        ):
            response = app.home(make_request(query_string=b"refresh=updated"))

        html = response.body.decode("utf-8")
        self.assertIn('action="/refresh"', html)
        self.assertIn("手动刷新", html)
        self.assertIn("汇率已更新", html)

    def test_refresh_endpoint_forces_refresh_and_redirects_with_status(self):
        with patch.object(
            app,
            "refresh_latest_rate_snapshot",
            return_value=(LATEST_RATE, PREVIOUS_RATE, None, "failed"),
            create=True,
        ) as refresh:
            response = app.refresh_rates()

        refresh.assert_called_once_with()
        self.assertEqual(303, response.status_code)
        self.assertEqual("/?refresh=failed", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
