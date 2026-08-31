from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from unionpay import get_latest_two_rates

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/")
def home(request: Request):
    latest, previous = get_latest_two_rates()

    rate = latest["rate"]
    latest_10000 = rate * 10000

    comparison = None

    if previous:
        previous_10000 = previous["rate"] * 10000
        change = latest_10000 - previous_10000
        change_pct = change / previous_10000 * 100

        comparison = {
            "date": previous["curDate"],
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
        },
    )