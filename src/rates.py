"""Risk-free rate: 91-day Treasury bill yield from FBIL (history) and RBI (latest)."""

import re
from datetime import date

import pandas as pd
import requests

from src.http_client import polite_get

# Public endpoint behind the FBIL website's T-bill benchmark page.
FBIL_TBILL_URL = "https://www.fbil.org.in/wasdm/tbill/fetchfiltered"
RBI_HOME_URL = "https://www.rbi.org.in/"

# The FBIL curve has no "91 Days" tenor; "3 Months" is the equivalent point.
FBIL_91_DAY_TENOR = "3 Months"


def fetch_fbil_tbill_curve(session: requests.Session, start: date, end: date) -> pd.DataFrame:
    """Daily FBIL T-bill curve (all tenors, % p.a.) between `start` and `end`."""
    params = {"fromDate": start.isoformat(), "toDate": end.isoformat(), "authenticated": "false"}
    response = polite_get(
        session, FBIL_TBILL_URL, params=params, headers={"Referer": "https://www.fbil.org.in/"}, delay=2.0
    )
    curve = pd.DataFrame(response.json())
    curve["date"] = pd.to_datetime(curve["processRunDate"]).dt.normalize()
    curve = curve.rename(columns={"tenorName": "tenor", "rate": "yield_pct"})
    return curve[["date", "tenor", "yield_pct"]].sort_values(["date", "tenor"]).reset_index(drop=True)


def fetch_rbi_latest_tbill(session: requests.Session) -> dict:
    """Latest 91-day T-bill auction cut-off yield, from the RBI home page 'Current Rates' box."""
    response = polite_get(session, RBI_HOME_URL, delay=2.0)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.text))
    yield_match = re.search(r"91 day T-bills\s*:\s*([\d.]+)\s*%", text)
    if not yield_match:
        raise RuntimeError("91-day T-bill yield not found on the RBI home page")
    return {"tenor": "91 day T-bill", "yield_pct": float(yield_match.group(1)), "basis": "cut-off at the last auction"}
