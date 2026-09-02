from datetime import datetime

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from unionpay import (
    JST,
    get_latest_rate_snapshot,
    get_rate_for_date,
    refresh_latest_rate_snapshot,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
APP_VERSION = "1.1.4"
REFRESH_MESSAGES = {
    "updated": "汇率已更新",
    "current": "刚刚已经刷新，无需重复操作",
    "throttled": "刚刚尝试过刷新，请稍后再试",
    "failed": "刷新失败，已继续使用缓存",
}


@app.get("/")
def home(request: Request):
    latest, previous, updated_at = get_latest_rate_snapshot()

    rate = latest["rate"]
    latest_10000 = rate * 10000
    updated_at_text = (
        f"{updated_at.astimezone(JST):%Y-%m-%d %H:%M} JST"
        if updated_at is not None
        else "未知"
    )

    comparison = None

    if previous:
        previous_10000 = previous["rate"] * 10000
        change = latest_10000 - previous_10000
        change_pct = change / previous_10000 * 100

        comparison = {
            "date": previous["curDate"],
            "rate": previous_10000,
            "change": change,
            "change_pct": change_pct,
        }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "latest": latest,
            "rate": rate,
            "latest_10000": latest_10000,
            "comparison": comparison,
            "updated_at_text": updated_at_text,
            "app_version": APP_VERSION,
            "refresh_status": request.query_params.get("refresh"),
            "refresh_message": REFRESH_MESSAGES.get(
                request.query_params.get("refresh")
            ),
        },
    )


@app.post("/refresh")
def refresh_rates():
    try:
        _latest, _previous, _updated_at, status = refresh_latest_rate_snapshot()
    except RuntimeError:
        status = "failed"

    return RedirectResponse(url=f"/?refresh={status}", status_code=303)


@app.get("/expenses")
def expenses(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="expenses.html",
        context={"app_version": APP_VERSION},
    )


@app.get("/api/rate")
def rate_for_date(date: str):
    try:
        requested_day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="日期格式无效") from error

    if requested_day > datetime.now(JST).date():
        raise HTTPException(status_code=400, detail="不能查询未来日期")

    try:
        result = get_rate_for_date(requested_day)
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502, detail="暂时无法取得银联汇率"
        ) from error

    if result is None:
        raise HTTPException(status_code=404, detail="14 天内没有可用汇率")

    return result
