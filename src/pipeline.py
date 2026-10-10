"""Data pipeline entry point.

    python -m src.pipeline            download the full window, rebuild all tables
    python -m src.pipeline --update   download only dates after the last one we hold

Both modes skip files already cached, then rebuild the parquet tables and the
data-quality report from everything on disk.
"""

import argparse
from datetime import date, timedelta

import pandas as pd

from src import build_tables, quality_report
from src.http_client import make_session
from src.ncdex import RAW_BHAVCOPY_DIR, download_bhavcopies
from src.rates import fbil_91_day_series, fetch_fbil_tbill_curve, fetch_rbi_latest_tbill

HISTORY_START = date(2025, 10, 10)
RAW_RATES_DIR = RAW_BHAVCOPY_DIR.parent / "rates"


def last_processed_date() -> date | None:
    futures_path = build_tables.PROCESSED_DIR / "futures.parquet"
    if not futures_path.exists():
        return None
    return pd.read_parquet(futures_path, columns=["date"])["date"].max().date()


def run(update_only: bool) -> None:
    today = date.today()
    session = make_session()

    last_date = last_processed_date() if update_only else None
    start = last_date + timedelta(days=1) if last_date else HISTORY_START
    print(f"1/4 Downloading NCDEX bhavcopies, {start} to {today}")
    outcome = download_bhavcopies(session, start, today)
    print("    " + ", ".join(f"{name}: {len(days)}" for name, days in outcome.items()))

    print("2/4 Building futures and spot tables")
    raw = build_tables.load_raw_futures()
    futures = build_tables.build_futures(raw)
    spot = build_tables.build_spot(raw)
    spot_mismatches = build_tables.find_spot_mismatches(raw)

    print("3/4 Fetching the 91-day T-bill yield (FBIL history, RBI latest)")
    trading_days = pd.DatetimeIndex(sorted(spot["date"].unique()))
    # Start a little early so the first trading day has a value to carry forward.
    curve = fetch_fbil_tbill_curve(session, HISTORY_START - timedelta(days=10), today)
    RAW_RATES_DIR.mkdir(parents=True, exist_ok=True)
    curve.to_csv(RAW_RATES_DIR / "fbil_tbill_curve.csv", index=False)
    rbi = fetch_rbi_latest_tbill(session)
    tbill = build_tables.build_tbill(fbil_91_day_series(curve), rbi["yield_pct"], trading_days)

    build_tables.write_tables(futures, spot, tbill)
    print(f"    futures {len(futures)} rows, spot {len(spot)} rows, tbill {len(tbill)} rows")

    print("4/4 Writing the data-quality report")
    report_path = quality_report.write_report(futures, spot, tbill, spot_mismatches, HISTORY_START, today)
    print(f"    {report_path.relative_to(build_tables.ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and clean the arbitrage monitor's data.")
    parser.add_argument("--update", action="store_true", help="fetch only dates after the last processed one")
    run(update_only=parser.parse_args().update)


if __name__ == "__main__":
    main()
