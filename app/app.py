"""Commodity Cash-Futures Arbitrage Monitor.

    streamlit run app/app.py
"""

import sys
from pathlib import Path

# Make the project root importable so the app can use the metrics engine in src/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

import components
import header
import loader
import sidebar
import theme
from tabs import cash_futures, convergence, economics, method, rollover, term_structure

TABS = {
    "Cash vs Futures": cash_futures,
    "Arb Economics": economics,
    "Term Structure": term_structure,
    "Convergence & Entry Timing": convergence,
    "Rollover & Capacity": rollover,
    "Method & Data": method,
}

st.set_page_config(page_title="Cash-Futures Arbitrage Monitor", page_icon="📈", layout="wide")
theme.register_template()
components.inject_css()

inputs = sidebar.render()
view = loader.build_view(inputs.commodity, inputs.start, inputs.end, inputs.costs, inputs.rules)

header.render(view)
for container, module in zip(st.tabs(list(TABS)), TABS.values()):
    with container:
        module.render(view)
