"""Overview: what the desk does, the three findings, and how to read the other tabs."""

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import theme
from formatting import DAGGER, pct, rs_crore
from loader import View
from tabs.convergence import bucket_figure

CHART_HEIGHT = 250

TAB_GUIDE = [
    ("Cash vs Futures", "how far futures sit from spot each day, and whether today is unusual."),
    ("Arb Economics", "what each side earns after costs, where the costs go, and every tradeable day."),
    ("Term Structure", "the shape of the futures curve and how steeply it pays to carry stock."),
    ("Convergence & Entry Timing", "how the basis decays into expiry and when a trade should be entered."),
    ("Rollover & Capacity", "what the roll costs and how much size the market takes."),
    ("Method & Data", "the trade, the sources, the checks and every cost assumption."),
]


def signal_share_figure(peers) -> go.Figure:
    """Share of days each side was tradeable, one bar per commodity."""
    series = [
        ("carry_days_pct", "Cash-and-carry", theme.CARRY),
        ("reverse_days_pct", "Reverse arb", theme.REVERSE),
        ("no_trade_days_pct", "Neither", theme.FAINT),
    ]
    figure = go.Figure()
    for column, label, colour in series:
        figure.add_bar(
            y=peers["name"], x=peers[column], name=label, orientation="h", marker=dict(color=colour, line=dict(color=theme.SURFACE, width=2)),
            text=[f"{value:.0f}%" for value in peers[column]], textposition="inside", insidetextanchor="middle", textangle=0,
            textfont=dict(size=12, color="white" if colour != theme.FAINT else theme.INK),
            hovertemplate=f"{label}: %{{x:.1f}}% of days<extra></extra>",
        )  # fmt: skip
    figure.update_layout(barmode="stack", bargap=0.35, hovermode="closest", legend=dict(font=dict(size=11), traceorder="normal"), uniformtext=dict(minsize=11, mode="show"))
    figure.update_xaxes(range=[0, 100], ticksuffix="%", showgrid=False)
    figure.update_yaxes(autorange="reversed")
    return figure


def limit_figure(peers) -> go.Figure:
    """Two small panels with their own scales: tradeable size, and how tightly contracts settle."""
    figure = make_subplots(rows=1, cols=2, horizontal_spacing=0.2, subplot_titles=("Capacity, Rs crore a day", "Settlement error, %"))
    figure.add_bar(
        x=peers["name"], y=peers["capacity_median_rs_crore"], marker_color=theme.FUTURES,
        text=[f"{value:.1f}" for value in peers["capacity_median_rs_crore"]], textposition="outside", row=1, col=1,
    )  # fmt: skip
    figure.add_bar(
        x=peers["name"], y=peers["convergence_error_pct"], marker_color=theme.SPOT,
        text=[f"{value:.2f}%" for value in peers["convergence_error_pct"]], textposition="outside", row=1, col=2,
    )  # fmt: skip
    figure.update_layout(showlegend=False, hovermode="closest", bargap=0.45)
    figure.update_yaxes(range=[0, peers["capacity_median_rs_crore"].max() * 1.3], row=1, col=1)
    figure.update_yaxes(range=[0, peers["convergence_error_pct"].max() * 1.4], ticksuffix="%", row=1, col=2)
    figure.update_annotations(font=dict(size=12, color=theme.MUTED))
    return figure


def compact(figure: go.Figure) -> go.Figure:
    """Tighten a full-size chart so it reads inside a card."""
    figure.update_traces(textfont=dict(size=12, color=theme.INK), cliponaxis=False)
    figure.update_layout(uniformtext=dict(minsize=12, mode="show"), bargap=0.15)  # stop Plotly shrinking the labels to fit
    figure.update_xaxes(tickangle=0, tickfont=dict(size=10))
    figure.update_yaxes(title="Basis, % of spot")
    return figure


def finding(number: int, title: str, body: str, figure: go.Figure, key: str) -> None:
    with st.container(border=True):
        st.markdown(f'<p class="finding-title">{number}. {title}</p><p class="finding-body">{body}</p>', unsafe_allow_html=True)
        figure.update_layout(height=CHART_HEIGHT, margin=dict(l=10, r=10, t=34, b=10))
        st.plotly_chart(figure, key=key, config={"displayModeBar": False})


def render(view: View) -> None:
    peers = view.peers
    own = peers.loc[peers["commodity"] == view.commodity].iloc[0]
    buckets = view.full["convergence_buckets"]
    by_bucket = buckets.set_index("dte_bucket")["mean_basis_pct"]
    slope = view.full["term_structure_daily"]["slope_pct_per_month"].mean()
    reverse_market = peers.loc[peers["reverse_days_pct"].idxmax()]

    st.markdown(
        '<p class="lede">A cash-futures arbitrage desk buys a commodity in the spot market, stores it in an exchange warehouse and sells futures '
        "against it, earning the gap between the two prices when that gap is wider than the cost of carrying the goods. "
        "This dashboard measures that gap for jeera and guar seed on NCDEX over the past year, after real costs.</p>",
        unsafe_allow_html=True,
    )

    first, second, third = st.columns(3)
    with first:
        finding(
            1, "The carry is earned early, not near expiry",
            f"{view.name} futures averaged {pct(by_bucket.get('46-60'), signed=True)} over spot 46 to 60 days out and "
            f"{pct(by_bucket.get('6-10'), signed=True)} at 6 to 10 days, while the curve sloped up {pct(slope)} a month.",
            compact(bucket_figure(buckets)), "overview_buckets",
        )  # fmt: skip
    with second:
        finding(
            2, f"{reverse_market['name']} is a reverse-arb market {DAGGER}",
            f"Share of days each side made money after all costs. {reverse_market['name']} paid stock holders on "
            f"{reverse_market['reverse_days_pct']:.0f}% of days; physical stock cannot be shorted, so the gap persists.",
            signal_share_figure(peers), "overview_shares",
        )  # fmt: skip
    with third:
        finding(
            3, "Size is the limit, not the model",
            f"Contracts settled within {pct(peers['convergence_error_pct'].mean())} of the 3-day average spot, but only about "
            f"{rs_crore(own['capacity_median_rs_crore'])} a day of {view.name.lower()} is tradeable at 10% of volume.",
            limit_figure(peers), "overview_limits",
        )  # fmt: skip

    st.markdown('<p class="question">How to read this dashboard</p>', unsafe_allow_html=True)
    st.markdown("\n".join(f"- **{tab}:** {purpose}" for tab, purpose in TAB_GUIDE))
