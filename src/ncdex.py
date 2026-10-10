"""NCDEX daily bhavcopy: futures prices, OI, volume, expiries and the spot price.

One GET per trading day returns a zip with two CSVs:
  * BhavCopy_NCD_CO_..._F_0000.csv  UDiFF format: settlement price, OI, volume,
                                    expiry dates and the underlying (spot) price.
  * FO_ddmmyy_FINAL.csv             legacy format: adds the ex-basis delivery centre.
"""

import io
import zipfile
from datetime import date

import pandas as pd
import requests

from src.http_client import polite_get

BHAVCOPY_URL = "https://www.ncdex.com/markets/bhavcopy"

# UDiFF column -> our name. Prices are Rs/quintal; OI and volume are in MT.
UDIFF_COLUMNS = {
    "TradDt": "trade_date",
    "TckrSymb": "symbol",
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
    if "zip" not in response.headers.get("Content-Type", ""):
        return None
    return response.content


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
