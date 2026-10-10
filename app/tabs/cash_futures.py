"""Tab 1: spot against futures, the basis between them, and how the basis is distributed."""

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import components
import theme
from formatting import pct, rs
from loader import View


def price_and_basis_figure(view: View, show_next: bool, show_far: bool) -> go.Figure:
    """Spot and futures on top; basis in Rs and in % below, on a shared time axis."""
    economics, basis = view.tables["economics"], view.tables["basis"]
    held = basis.loc[basis["open_interest"] >= view.rules.min_oi]

    figure = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.56, 0.22, 0.22],
        subplot_titles=("Price (Rs per quintal)", "Basis (Rs per quintal)", "Basis (% of spot)"),
    )  # fmt: skip
    figure.add_scatter(x=economics["date"], y=economics["spot"], name="Spot", line=dict(color=theme.SPOT, width=2), row=1, col=1)
    figure.add_scatter(
        x=economics["date"], y=economics["settlement_price"], name="Active futures", line=dict(color=theme.FUTURES, width=2), row=1, col=1
    )
    extra = {"next": (show_next, "Next month", "dash"), "far": (show_far, "Far month", "dot")}
    for rank, (term, (shown, label, dash)) in zip((2, 3), extra.items()):
        if shown:
            contract = held.loc[held["contract_rank"] == rank]
            figure.add_scatter(
                x=contract["date"], y=contract["settlement_price"], name=label,
                line=dict(color=theme.hex_to_rgba(theme.FUTURES, 0.55), width=1.5, dash=dash), row=1, col=1,
            )  # fmt: skip

    # Roll points: the first day each new contract becomes the active one.
    rolls = economics.loc[economics["contract_symbol"] != economics["contract_symbol"].shift()].iloc[1:]
    figure.add_scatter(
        x=rolls["date"], y=rolls["settlement_price"], name="Roll to next contract", mode="markers",
        marker=dict(symbol="diamond", size=8, color="white", line=dict(color=theme.FUTURES, width=2)), row=1, col=1,
    )  # fmt: skip
    figure.add_bar(x=economics["date"], y=economics["basis"], name="Basis (Rs)", marker_color=theme.FUTURES, showlegend=False, row=2, col=1)
    figure.add_scatter(
        x=economics["date"], y=economics["basis_pct"], name="Basis (%)", line=dict(color=theme.FUTURES, width=1.5), showlegend=False, row=3, col=1
    )
    figure.add_hline(y=0, line_width=1, line_color=theme.FAINT, row=3, col=1)

    expiries = basis.loc[basis["date"] == basis["expiry"], "expiry"].unique()
    for row in (1, 2, 3):
        components.add_expiry_lines(figure, expiries, row=row)
    figure.update_annotations(font=dict(size=12, color=theme.MUTED), xanchor="left", x=0)
    figure.update_layout(bargap=0, legend=dict(y=1.06))
    return figure


def distribution_figure(view: View) -> go.Figure:
    economics = view.tables["economics"]
    current = economics["basis_pct"].iloc[-1]
    figure = go.Figure(go.Histogram(x=economics["basis_pct"], nbinsx=40, marker_color=theme.FUTURES, marker_line=dict(color=theme.SURFACE, width=1), name="Days"))
    figure.add_vline(x=current, line_width=2, line_color=theme.SPOT, annotation_text=f"today {pct(current, signed=True)}", annotation_position="top")
    figure.add_vline(x=0, line_width=1, line_color=theme.FAINT)
    figure.update_layout(xaxis_title="Basis, % of spot", yaxis_title="Trading days", hovermode="closest", bargap=0.02)
    return figure


def render(view: View) -> None:
    economics = view.tables["economics"]
    basis_pct = economics["basis_pct"]
    current, average = basis_pct.iloc[-1], basis_pct.mean()
    above_spot = (economics["basis"] > 0).mean() * 100

    left, right, _ = st.columns([1, 1, 4])
    show_next = left.checkbox("Show next month", key="show_next")
    show_far = right.checkbox("Show far month", key="show_far")
    components.chart(
        price_and_basis_figure(view, show_next, show_far),
        "How far do futures trade from spot, and when?",
        f"Futures closed above spot on {above_spot:.0f}% of days. The basis ranged from {pct(basis_pct.min(), signed=True)} to "
        f"{pct(basis_pct.max(), signed=True)} and is {pct(current, signed=True)} today, against a one-year average of {pct(average, signed=True)}. "
        "Dotted lines mark expiries; diamonds mark the roll to the next contract.",
        key="price_basis", height=620,
    )  # fmt: skip

    percentile = (basis_pct < current).mean() * 100
    components.chart(
        distribution_figure(view),
        "Is today's basis unusual?",
        f"Today's basis of {pct(current, signed=True)} is higher than on {percentile:.0f}% of days in the period. "
        f"Half of all days fell between {pct(basis_pct.quantile(0.25), signed=True)} and {pct(basis_pct.quantile(0.75), signed=True)}.",
        key="basis_distribution", height=300,
    )  # fmt: skip

    components.kpi_row(
        [
            dict(label="Average basis", value=pct(average, signed=True), sub=rs(economics["basis"].mean(), signed=True)),
            dict(label="Lowest", value=pct(basis_pct.min(), signed=True), sub=rs(economics["basis"].min(), signed=True)),
            dict(label="Highest", value=pct(basis_pct.max(), signed=True), sub=rs(economics["basis"].max(), signed=True)),
            dict(label="Today vs average", value=f"{current - average:+.2f} pts", sub=f"today {pct(current, signed=True)}"),
            dict(label="Days futures above spot", value=pct(above_spot), sub=f"{len(economics)} trading days"),
        ]
    )
