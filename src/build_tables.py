"""Turn the cached raw downloads into the three clean tables the dashboard reads.

    data/processed/futures.parquet   one row per date x contract
    data/processed/spot.parquet      one row per date x commodity
    data/processed/tbill.parquet     one row per trading date
"""

import tomllib
from pathlib import Path

import pandas as pd

from src.ncdex import RAW_BHAVCOPY_DIR, read_bhavcopy_zip, tidy_futures

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"

FUTURES_COLUMNS = [
    "date", "commodity", "contract_symbol", "expiry", "days_to_expiry",
    "open", "high", "low", "close", "settlement_price",
    "volume", "open_interest", "spot", "zero_oi_flag", "contract_rank",
]  # fmt: skip


def load_commodities() -> dict:
    return tomllib.loads((ROOT / "config" / "commodities.toml").read_text())


def load_raw_futures(raw_dir: Path = RAW_BHAVCOPY_DIR) -> pd.DataFrame:
    """Read every cached bhavcopy and keep the futures rows of our commodities."""
    symbol_to_commodity = {c["futures_symbol"]: name for name, c in load_commodities().items()}
    days = []
    for zip_path in sorted(raw_dir.glob("bhavcopy_*.zip")):
        udiff, legacy = read_bhavcopy_zip(zip_path.read_bytes())
        days.append(tidy_futures(udiff, legacy, list(symbol_to_commodity)))
    raw = pd.concat(days, ignore_index=True)
    raw["commodity"] = raw["symbol"].map(symbol_to_commodity)
    return raw


def build_futures(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per date x contract. Prices in Rs/quintal, volume and OI in MT."""
    futures = raw.rename(
        columns={
            "trade_date": "date",
            "expiry_date": "expiry",
            "settle_price": "settlement_price",
            "volume_mt": "volume",
            "open_interest_mt": "open_interest",
            "spot_price": "spot",
        }
    )
    futures["days_to_expiry"] = (futures["expiry"] - futures["date"]).dt.days
    futures["zero_oi_flag"] = futures["open_interest"] == 0

    # Rank 1 = near month, 2 = next, 3+ = far, among contracts still alive on that date.
    alive = futures["days_to_expiry"] >= 0
    futures["contract_rank"] = (
        futures[alive].groupby(["date", "commodity"])["expiry"].rank(method="first").astype("Int64")
    )
    return futures[FUTURES_COLUMNS].sort_values(["commodity", "date", "expiry"]).reset_index(drop=True)


def find_spot_mismatches(raw: pd.DataFrame) -> pd.DataFrame:
    """Dates where contracts of one commodity disagree on the underlying (spot) price."""
    spread = raw.groupby(["trade_date", "commodity"])["spot_price"].agg(["nunique", "min", "max"])
    return spread[spread["nunique"] > 1].reset_index()


def build_spot(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per date x commodity, taking the near-month contract's spot price."""
    near = raw.sort_values("expiry_date").groupby(["trade_date", "commodity"], as_index=False).first()
    spot = near.rename(columns={"trade_date": "date", "spot_price": "spot"})
    spot["basis_centre"] = spot["basis_centre"].str.title()
    return spot[["date", "commodity", "spot", "basis_centre"]].sort_values(["commodity", "date"]).reset_index(drop=True)


def build_tbill(fbil_91_day: pd.Series, rbi_latest_yield: float, trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    """91-day T-bill yield on every trading day, with the origin of each value.

    source = fbil      published by FBIL for that date
             ffill     FBIL gap inside the window, carried forward from the previous value
             rbi_fill  after FBIL's last published date; latest RBI auction cut-off yield
    """
    tbill = pd.DataFrame({"date": trading_days})
    tbill["yield_pct"] = fbil_91_day.reindex(trading_days).to_numpy()
    tbill["source"] = tbill["yield_pct"].notna().map({True: "fbil", False: "ffill"})

    after_fbil = tbill["date"] > fbil_91_day.index.max()
    tbill.loc[after_fbil, ["yield_pct", "source"]] = [rbi_latest_yield, "rbi_fill"]
    tbill["yield_pct"] = tbill["yield_pct"].ffill()
    return tbill


def write_tables(futures: pd.DataFrame, spot: pd.DataFrame, tbill: pd.DataFrame) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    futures.to_parquet(PROCESSED_DIR / "futures.parquet", index=False)
    spot.to_parquet(PROCESSED_DIR / "spot.parquet", index=False)
    tbill.to_parquet(PROCESSED_DIR / "tbill.parquet", index=False)
