"""Load the cleaned tables and run the metrics engine for the dashboard.

Both steps are cached. The metrics are keyed on the commodity, date range and
every desk input, so moving a slider re-runs only the cost model.
"""

from dataclasses import astuple, dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src import metrics, settings
from src.metrics import CostInputs, Rules

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"

SHORT_DATED_DAYS = 15  # below this, annualised figures are shown greyed with a footnote


@dataclass
class View:
    """Everything a tab needs to draw one commodity."""

    commodity: str  # config key: JEERA or GUARSEED
    name: str  # display name
    details: dict  # the commodity's entry in commodities.toml
    costs: CostInputs
    rules: Rules
    tables: dict[str, pd.DataFrame]
    snapshot: dict


@st.cache_data
def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return tuple(pd.read_parquet(PROCESSED_DIR / f"{name}.parquet") for name in ("futures", "spot", "tbill"))


@st.cache_data
def load_config() -> tuple[dict, dict, dict]:
    """commodities.toml, carry_costs.toml and assumptions.toml."""
    return tuple(settings.read_config(name) for name in ("commodities.toml", "carry_costs.toml", "assumptions.toml"))


@st.cache_data(show_spinner=False)
def compute_tables(commodity: str, start: date, end: date, cost_values: tuple, rule_values: tuple) -> dict[str, pd.DataFrame]:
    """Every metric table for one commodity and date range. All logic lives in src/metrics.py."""
    costs, rules = CostInputs(*cost_values), Rules(*rule_values)
    futures, spot, tbill = load_tables()

    def in_range(table: pd.DataFrame) -> pd.DataFrame:
        return table.loc[table["date"].between(pd.Timestamp(start), pd.Timestamp(end))]

    futures, spot, tbill = in_range(futures), in_range(spot), in_range(tbill)
    futures, spot = futures.loc[futures["commodity"] == commodity], spot.loc[spot["commodity"] == commodity]

    basis = metrics.basis_table(futures, spot, rules)
    economics = metrics.daily_economics(basis, tbill, costs, rules)
    settlements = metrics.expiry_settlements(basis)
    log = metrics.opportunity_log(economics, settlements)
    paths = metrics.convergence_paths(basis, rules)
    curve = metrics.term_structure(basis, rules)
    curve_daily = metrics.term_structure_daily(curve)
    timing = metrics.entry_timing(basis, tbill, costs, rules)
    lot_size = settings.load_commodities()[commodity]["lot_size_mt"]
    return {
        "basis": basis,
        "economics": economics,
        "opportunities": log,
        "opportunity_summary": metrics.opportunity_summary(economics, log),
        "expiry_settlements": settlements,
        "convergence_paths": paths,
        "convergence_buckets": metrics.convergence_buckets(paths),
        "term_structure": curve,
        "term_structure_daily": curve_daily,
        "term_structure_monthly": metrics.term_structure_monthly(curve_daily),
        "rollover": metrics.rollover(basis),
        "liquidity": metrics.liquidity(basis, lot_size, rules),
        "entry_timing": timing,
        "entry_timing_buckets": metrics.entry_timing_buckets(timing),
    }


def build_view(commodity: str, start: date, end: date, costs: CostInputs, rules: Rules) -> View:
    details = load_config()[0][commodity]
    name = details["name"].split(" (")[0]
    tables = compute_tables(commodity, start, end, astuple(costs), astuple(rules))
    snapshot = metrics.snapshot(name, tables["economics"], tables["liquidity"])
    return View(commodity, name, details, costs, rules, tables, snapshot)
