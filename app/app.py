"""Commodity Cash-Futures Arbitrage Monitor.

    streamlit run app/app.py
"""

import sys
from pathlib import Path

# Make the project root importable so the app can use the metrics engine in src/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

import header
import loader
import sidebar
import theme
from tabs import backtest, cash_futures, convergence, economics, method, overview, rollover, signal_finder, term_structure

# Tab label -> (icon, module). Labels are what the tests and the guide refer to.
TABS = {
    "Overview": (":material/dashboard:", overview),
    "Signal Finder": (":material/target:", signal_finder),
    "Historical Backtest": (":material/history:", backtest),
    "Cash vs Futures": (":material/show_chart:", cash_futures),
    "Arb Economics": (":material/payments:", economics),
    "Term Structure": (":material/stacked_line_chart:", term_structure),
    "Convergence & Entry Timing": (":material/timer:", convergence),
    "Rollover & Capacity": (":material/sync_alt:", rollover),
    "Method & Data": (":material/menu_book:", method),
}

st.set_page_config(page_title="Cash-Futures Arbitrage Monitor", page_icon="📈", layout="wide")

mode = theme.LIGHT if st.session_state.get("light_theme") else theme.DARK
theme.sync_streamlit_theme(mode)
theme.activate(mode)

inputs = sidebar.render()
view = loader.build_view(inputs.commodity, inputs.start, inputs.end, inputs.costs, inputs.rules)

header.render(view)
containers = st.tabs([f"{icon} {label}" for label, (icon, _) in TABS.items()])
for container, (_, module) in zip(containers, TABS.values()):
    with container:
        module.render(view)
