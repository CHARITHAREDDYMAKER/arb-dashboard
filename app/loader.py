"""Load the cleaned tables and run the metrics engine for the dashboard.

Metrics are always computed on the FULL history, so rolling averages and the
header never depend on the date range; the range only filters what is shown.
Runs are cached on the commodity and every desk input, so moving a slider
re-runs only the cost model.
"""

from dataclasses import astuple, dataclass, replace
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src import metrics, settings
from src.metrics import SHORT_DATED_DAYS, CostInputs, Rules  # noqa: F401  (SHORT_DATED_DAYS is re-exported to the tabs)

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


@dataclass
class View:
    """Everything a tab needs to draw one commodity."""

    commodity: str  # config key: JEERA or GUARSEED
    name: str  # display name
    details: dict  # the commodity's entry in commodities.toml
    costs: CostInputs
    rules: Rules
    full: dict[str, pd.DataFrame]  # metric tables over the whole history
    tables: dict[str, pd.DataFrame]  # the same tables limited to the chosen date range
    snapshot: dict  # latest trading day, whatever the date range
    latest: pd.Series  # latest trading day's row of the economics table
    peers: pd.DataFrame  # one summary row per commodity, for the overview
    gst_what_if: pd.DataFrame  # tradeable days with and without GST on warehouse charges


@st.cache_data
def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return tuple(pd.read_parquet(PROCESSED_DIR / f"{name}.parquet") for name in ("futures", "spot", "tbill"))


@st.cache_data
def load_config() -> tuple[dict, dict, dict]:
    """commodities.toml, carry_costs.toml and assumptions.toml."""
    return tuple(settings.read_config(name) for name in ("commodities.toml", "carry_costs.toml", "assumptions.toml"))


def display_name(commodity: str) -> str:
    return load_config()[0][commodity]["name"].split(" (")[0]


@st.cache_data(show_spinner=False)
def compute_tables(commodity: str, cost_values: tuple, rule_values: tuple) -> dict[str, pd.DataFrame]:
    """Every metric table for one commodity over the full history. All logic lives in src/metrics.py."""
    costs, rules = CostInputs(*cost_values), Rules(*rule_values)
    futures, spot, tbill = load_tables()
    futures, spot = futures.loc[futures["commodity"] == commodity], spot.loc[spot["commodity"] == commodity]

    basis = metrics.basis_table(futures, spot, rules)
    economics = metrics.daily_economics(basis, tbill, costs, rules)
    lot_size = load_config()[0][commodity]["lot_size_mt"]
    tables = {
        "basis": basis,
        "economics": economics,
        "expiry_settlements": metrics.expiry_settlements(basis),
        "convergence_paths": metrics.convergence_paths(basis, rules),
        "term_structure": metrics.term_structure(basis, rules),
        "rollover": metrics.rollover(basis),
        "liquidity": metrics.liquidity(basis, lot_size, rules),
        "entry_timing": metrics.entry_timing(basis, tbill, costs, rules),
    }
    tables["opportunities"] = metrics.opportunity_log(economics, tables["expiry_settlements"])
    tables["term_structure_daily"] = metrics.term_structure_daily(tables["term_structure"])
    return add_summaries(tables)


def add_summaries(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Tables that summarise other tables; rebuilt whenever the date range changes."""
    return {
        **tables,
        "opportunity_summary": metrics.opportunity_summary(tables["economics"], tables["opportunities"]),
        "convergence_buckets": metrics.convergence_buckets(tables["convergence_paths"]),
        "term_structure_monthly": metrics.term_structure_monthly(tables["term_structure_daily"]),
        "entry_timing_buckets": metrics.entry_timing_buckets(tables["entry_timing"]),
    }


# The date column each table is filtered on.
DATE_COLUMNS = {
    "basis": "date", "economics": "date", "convergence_paths": "date", "term_structure": "date",
    "term_structure_daily": "date", "rollover": "date", "liquidity": "date", "entry_timing": "date",
    "opportunities": "entry_date", "expiry_settlements": "expiry",
}  # fmt: skip


def limit_to_range(full: dict[str, pd.DataFrame], start: date, end: date) -> dict[str, pd.DataFrame]:
    """The same tables showing only rows inside the date range. Values are NOT recomputed."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    shown = {name: full[name].loc[full[name][column].between(start, end)].reset_index(drop=True) for name, column in DATE_COLUMNS.items()}
    return add_summaries(shown)


def peer_summary(costs_by_commodity: dict[str, CostInputs], rules: Rules) -> pd.DataFrame:
    """One row per commodity: tradeable-day shares, capacity and convergence error over the full history."""
    rows = []
    for commodity, costs in costs_by_commodity.items():
        tables = compute_tables(commodity, astuple(costs), astuple(rules))
        shares = metrics.signal_shares(tables["economics"])
        latest, previous = tables["economics"].iloc[-1], tables["economics"].iloc[-2]
        rows.append(
            {
                "commodity": commodity,
                "name": display_name(commodity),
                "spot": latest["spot"],
                "spot_change_pct": (latest["spot"] / previous["spot"] - 1) * 100,
                "active_futures": latest["settlement_price"],
                "basis_pct": latest["basis_pct"],
                "signal": latest["tradeable_signal"],
                "carry_days_pct": shares[metrics.SIGNAL_CASH_AND_CARRY],
                "reverse_days_pct": shares[metrics.SIGNAL_REVERSE],
                "no_trade_days_pct": shares[metrics.SIGNAL_NONE],
                "capacity_median_rs_crore": tables["liquidity"]["capacity_rs_crore"].median(),
                "convergence_error_pct": tables["expiry_settlements"]["convergence_error_pct"].abs().mean(),
                "expired_contracts": len(tables["expiry_settlements"]),
            }
        )
    return pd.DataFrame(rows)


def gst_what_if(commodity: str, costs: CostInputs, rules: Rules) -> pd.DataFrame:
    """Tradeable-day shares with 18% GST on storage and assaying switched on and off."""
    rows = []
    for applied in (True, False):
        case = replace(costs, gst_on_storage_and_assaying=applied)
        shares = metrics.signal_shares(compute_tables(commodity, astuple(case), astuple(rules))["economics"])
        rows.append(
            {
                "gst_applied": applied,
                "is_current": applied == costs.gst_on_storage_and_assaying,
                "carry_days_pct": shares[metrics.SIGNAL_CASH_AND_CARRY],
                "reverse_days_pct": shares[metrics.SIGNAL_REVERSE],
            }
        )
    return pd.DataFrame(rows)


def build_view(commodity: str, start: date, end: date, costs_by_commodity: dict[str, CostInputs], rules: Rules) -> View:
    costs = costs_by_commodity[commodity]
    full = compute_tables(commodity, astuple(costs), astuple(rules))
    return View(
        commodity=commodity,
        name=display_name(commodity),
        details=load_config()[0][commodity],
        costs=costs,
        rules=rules,
        full=full,
        tables=limit_to_range(full, start, end),
        snapshot=metrics.snapshot(display_name(commodity), full["economics"], full["liquidity"]),
        latest=full["economics"].iloc[-1],
        peers=peer_summary(costs_by_commodity, rules),
        gst_what_if=gst_what_if(commodity, costs, rules),
    )
