"""Compute every metric table from the cleaned data and save them as parquet.

    python -m src.build_metrics

Reads data/processed/{futures,spot,tbill}.parquet and the config files, writes
data/processed/metrics/*.parquet and reports/phase3_metrics.md.
"""

from pathlib import Path

import pandas as pd

from src import metrics, metrics_report, settings

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"
METRICS_DIR = PROCESSED_DIR / "metrics"


def compute_all(futures: pd.DataFrame, spot: pd.DataFrame, tbill: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every metric table, keyed by the file name it is saved under."""
    rules = settings.load_rules()
    commodities = settings.load_commodities()

    basis = metrics.basis_table(futures, spot, rules)
    settlements = metrics.expiry_settlements(basis)
    paths = metrics.convergence_paths(basis, rules)
    curve = metrics.term_structure(basis, rules)
    curve_daily = metrics.term_structure_daily(curve)

    # Cost inputs and lot sizes differ by commodity, so these are built one at a time.
    economics, liquidity, logs, summaries, snapshots = [], [], [], [], []
    for commodity, details in commodities.items():
        own_basis = basis.loc[basis["commodity"] == commodity]
        own_economics = metrics.daily_economics(own_basis, tbill, settings.load_cost_inputs(commodity), rules)
        own_liquidity = metrics.liquidity(own_basis, details["lot_size_mt"], rules)
        own_log = metrics.opportunity_log(own_economics, settlements)
        economics.append(own_economics)
        liquidity.append(own_liquidity)
        logs.append(own_log)
        summaries.append(metrics.opportunity_summary(own_economics, own_log))
        snapshots.append(metrics.snapshot(details["name"].split(" (")[0], own_economics, own_liquidity))

    return {
        "basis": basis,
        "economics": pd.concat(economics, ignore_index=True),
        "opportunities": pd.concat(logs, ignore_index=True),
        "opportunity_summary": pd.concat(summaries, ignore_index=True),
        "expiry_settlements": settlements,
        "convergence_paths": paths,
        "convergence_buckets": metrics.convergence_buckets(paths),
        "term_structure": curve,
        "term_structure_daily": curve_daily,
        "term_structure_monthly": metrics.term_structure_monthly(curve_daily),
        "rollover": metrics.rollover(basis),
        "liquidity": pd.concat(liquidity, ignore_index=True),
        "snapshot": pd.DataFrame(snapshots),
    }


def main() -> None:
    futures = pd.read_parquet(PROCESSED_DIR / "futures.parquet")
    spot = pd.read_parquet(PROCESSED_DIR / "spot.parquet")
    tbill = pd.read_parquet(PROCESSED_DIR / "tbill.parquet")

    tables = compute_all(futures, spot, tbill)
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_parquet(METRICS_DIR / f"{name}.parquet", index=False)
        print(f"  {name:<24} {len(table):>5} rows")

    report_path = metrics_report.write_report(tables, tbill)
    print(f"Report written to {report_path.name}")


if __name__ == "__main__":
    main()
