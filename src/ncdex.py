"""NCDEX daily bhavcopy: futures prices, OI, volume, expiries and the spot price.

One GET per trading day returns a zip with two CSVs:
  * BhavCopy_NCD_CO_..._F_0000.csv  UDiFF format: settlement price, OI, volume,
                                    expiry dates and the underlying (spot) price.
  * FO_ddmmyy_FINAL.csv             legacy format: adds the ex-basis delivery centre.
"""

import io
import time
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from src.http_client import polite_get

BHAVCOPY_URL = "https://www.ncdex.com/markets/bhavcopy"
RAW_BHAVCOPY_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "bhavcopy"

# Weekdays that returned no file (exchange holidays), remembered so reruns skip them.
NO_FILE_LOG = "no_file_dates.txt"
# A missing file for a very recent date may just be "not published yet", so only
# dates older than this are remembered as holidays.
HOLIDAY_CONFIRM_DAYS = 3
# NCDEX drops connections after a burst of requests; wait this long before resuming.
COOLDOWN_SECONDS = 120
BROWSER_CHECK_MARKER = "Fingerprint.start"

# UDiFF column -> our name. Prices are Rs/quintal; OI and volume are in MT.
UDIFF_COLUMNS = {
    "TradDt": "trade_date",
    "TckrSymb": "symbol",
    "FinInstrmNm": "contract_symbol",  # e.g. JEERAUNJHA20NOV2026
    "FininstrmActlXpryDt": "expiry_date",  # actual expiry (XpryDt can fall on a holiday)
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "SttlmPric": "settle_price",
    "UndrlygPric": "spot_price",
    "OpnIntrst": "open_interest_mt",
    "TtlTradgVol": "volume_mt",
    "TtlTrfVal": "traded_value_rs",
    "TtlNbOfTxsExctd": "trades",
    "NewBrdLotQty": "lot_size_mt",
}


def fetch_bhavcopy_zip(session: requests.Session, day: date) -> bytes | None:
    """Download the final bhavcopy zip for `day`.

    Returns None on weekends and holidays, when NCDEX serves the HTML page
    instead of a zip.
    """
    params = {
        "file_type_both": "final",
        "type_both": "bhavcopy",
        "bhavcopy_for": "both",
        "filedate_both": day.strftime("%d-%m-%Y"),
        "format_both": "csv_file",
    }
    response = polite_get(session, BHAVCOPY_URL, params=params, headers={"Referer": BHAVCOPY_URL})
    if "zip" in response.headers.get("Content-Type", ""):
        return response.content
    if BROWSER_CHECK_MARKER in response.text:
        # Not a holiday: the site served its browser check instead of the bhavcopy page.
        raise RuntimeError(f"NCDEX served a browser check for {day}")
    return None


def bhavcopy_path(day: date, raw_dir: Path = RAW_BHAVCOPY_DIR) -> Path:
    return raw_dir / f"bhavcopy_{day:%Y%m%d}.zip"


def download_bhavcopies(
    session: requests.Session, start: date, end: date, raw_dir: Path = RAW_BHAVCOPY_DIR
) -> dict[str, list[date]]:
    """Download every weekday bhavcopy from `start` to `end` into `raw_dir`.

    Resumable: dates already on disk, and dates known to be holidays, are skipped.
    Returns the dates in each outcome: downloaded, cached, no_file, failed.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    no_file_log = raw_dir / NO_FILE_LOG
    known_holidays = set(no_file_log.read_text().split()) if no_file_log.exists() else set()

    weekdays = [d for d in pd.date_range(start, end).date if d.weekday() < 5]
    outcome: dict[str, list[date]] = {"downloaded": [], "cached": [], "no_file": [], "failed": []}

    for number, day in enumerate(weekdays, start=1):
        progress = f"[{number:>3}/{len(weekdays)}] {day}"
        if bhavcopy_path(day, raw_dir).exists():
            outcome["cached"].append(day)
            continue
        if day.isoformat() in known_holidays:
            outcome["no_file"].append(day)
            continue

        content, fetched = None, False
        for attempt in range(3):
            try:
                content = fetch_bhavcopy_zip(session, day)
                fetched = True
                break
            except RuntimeError as error:
                print(f"{progress}  connection problem ({error}); cooling down {COOLDOWN_SECONDS}s", flush=True)
                time.sleep(COOLDOWN_SECONDS * (attempt + 1))

        if not fetched:
            outcome["failed"].append(day)
            print(f"{progress}  FAILED, will retry on the next run", flush=True)
        elif content is None:
            outcome["no_file"].append(day)
            if (date.today() - day).days > HOLIDAY_CONFIRM_DAYS:
                with no_file_log.open("a") as log:
                    log.write(f"{day.isoformat()}\n")
            print(f"{progress}  no file (holiday)", flush=True)
        else:
            bhavcopy_path(day, raw_dir).write_bytes(content)
            outcome["downloaded"].append(day)
            print(f"{progress}  downloaded {len(content) // 1024} KB", flush=True)
    return outcome


def read_bhavcopy_zip(content: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (udiff, legacy) DataFrames exactly as NCDEX publishes them."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        udiff_name = next(n for n in names if n.startswith("BhavCopy"))
        legacy_name = next(n for n in names if n.startswith("FO_"))
        udiff = pd.read_csv(archive.open(udiff_name))
        legacy = pd.read_csv(archive.open(legacy_name))
    return udiff, legacy


def tidy_futures(udiff: pd.DataFrame, legacy: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """One row per (trade date, symbol, expiry) for the futures of `symbols`."""
    is_future = udiff["FinInstrmTp"] == "COF"
    futures = udiff.loc[is_future & udiff["TckrSymb"].isin(symbols), list(UDIFF_COLUMNS)]
    futures = futures.rename(columns=UDIFF_COLUMNS)
    for column in ("trade_date", "expiry_date"):
        futures[column] = pd.to_datetime(futures[column])

    centres = (
        legacy.loc[legacy["Instrument Type"] == "FUTCOM"]
        .drop_duplicates("Symbol")
        .set_index("Symbol")["Ex-Basis Delivery Centre"]
    )
    futures["basis_centre"] = futures["symbol"].map(centres)
    return futures.sort_values(["symbol", "expiry_date"]).reset_index(drop=True)
