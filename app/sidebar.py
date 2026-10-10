"""Sidebar: commodity, date range and the desk inputs that drive the cost model."""

from dataclasses import dataclass, replace
from datetime import date

import streamlit as st

import loader
from formatting import DAGGER, long_date
from src import settings
from src.metrics import CostInputs, Rules

COMMODITY_LABELS = {"JEERA": "Jeera", "GUARSEED": "Guar seed"}


@dataclass
class Inputs:
    commodity: str
    start: date
    end: date
    costs: dict[str, CostInputs]  # one set of cost inputs per commodity; desk inputs apply to both
    rules: Rules


def default_inputs() -> dict:
    """Default value of every desk input, keyed by its widget key."""
    assumed, rules = loader.load_config()[2]["costs"], settings.load_rules()
    return {
        "brokerage": float(assumed["brokerage_rs_per_lakh"]),
        "funding_spread": float(assumed["funding_spread_pct"]),
        "handling": float(assumed["handling"]["rs_per_quintal"]),
        "gst_on_storage": bool(assumed["gst_on_storage_and_assaying"]),
        "cold_storage": False,
        "signal_threshold": float(rules.signal_threshold_pct),
        "roll_dte": int(rules.roll_dte_threshold),
    }


def reset_inputs() -> None:
    st.session_state.update(default_inputs())


def render() -> Inputs:
    futures, _, tbill = loader.load_tables()
    first_day, last_day = futures["date"].min().date(), futures["date"].max().date()
    for key, value in default_inputs().items():
        st.session_state.setdefault(key, value)
        # Streamlit forgets a widget's value when the widget is not drawn (the cold-storage
        # toggle is hidden for guar seed). Re-assigning the key keeps the value alive.
        st.session_state[key] = st.session_state[key]

    with st.sidebar:
        st.markdown("### Arbitrage Monitor")
        st.toggle("Light theme", key="light_theme", help="The dashboard is dark by default. On a shared deployment this switches the theme for every viewer.")
        keys = list(COMMODITY_LABELS)
        requested = st.query_params.get("commodity", keys[0])  # lets a link open on a given commodity
        commodity = st.radio(
            "Commodity", keys, index=keys.index(requested) if requested in keys else 0,
            format_func=COMMODITY_LABELS.get, horizontal=True,
        )  # fmt: skip
        chosen = st.date_input("Date range", (first_day, last_day), min_value=first_day, max_value=last_day, format="DD/MM/YYYY")
        start, end = chosen if len(chosen) == 2 else (first_day, last_day)

        with st.expander("Desk inputs", expanded=True):
            st.number_input("Brokerage (Rs per lakh)", 0.0, 100.0, step=1.0, key="brokerage")
            st.number_input("Funding spread over T-bill (% p.a.)", 0.0, 10.0, step=0.25, key="funding_spread")
            st.number_input(f"Handling {DAGGER} (Rs per quintal)", 0.0, 100.0, step=5.0, key="handling")
            st.toggle(f"18% GST on storage and assaying {DAGGER}", key="gst_on_storage")
            if settings.has_cold_storage(commodity):
                st.toggle("Jeera in cold storage (Rs 8.5 per MT per day)", key="cold_storage")
            st.number_input("Signal threshold (% of spot)", 0.0, 5.0, step=0.05, key="signal_threshold")
            st.number_input("Roll when near month has fewer than (days)", 0, 20, step=1, key="roll_dte")
            st.button("Reset to defaults", on_click=reset_inputs, width="stretch")

        latest_rate = tbill.sort_values("date").iloc[-1]
        source = {"fbil": "FBIL", "ffill": "FBIL, carried forward", "rbi_fill": "RBI fill"}[latest_rate["source"]]
        st.caption(
            f"Data to {long_date(futures['date'].max())}. "
            f"91-day T-bill {latest_rate['yield_pct']:.2f}% ({source}). "
            "Prices in Rs per quintal."
        )

    state = st.session_state
    costs = {
        key: replace(
            settings.load_cost_inputs(key, cold_storage=state["cold_storage"] and settings.has_cold_storage(key)),
            brokerage_rs_per_lakh=state["brokerage"],
            funding_spread_pct=state["funding_spread"],
            handling_rs_per_quintal=state["handling"],
            gst_on_storage_and_assaying=state["gst_on_storage"],
        )
        for key in COMMODITY_LABELS
    }
    rules = replace(settings.load_rules(), signal_threshold_pct=state["signal_threshold"], roll_dte_threshold=state["roll_dte"])
    return Inputs(commodity, start, end, costs, rules)
