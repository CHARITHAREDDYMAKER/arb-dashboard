"""Tab 4: how the basis behaves as a contract nears expiry, and what that means for entry timing."""

import plotly.graph_objects as go
import streamlit as st

import components
import theme
from formatting import DAGGER, pct, rs
from loader import View
from src.metrics import CONVERGENCE_BUCKET_LABELS

BUCKETS_FAR_TO_NEAR = CONVERGENCE_BUCKET_LABELS[::-1]  # time runs left to right, towards expiry


def bucket_figure(buckets) -> go.Figure:
    buckets = buckets.set_index("dte_bucket").reindex(BUCKETS_FAR_TO_NEAR).dropna(subset=["mean_basis_pct"]).reset_index()
    figure = go.Figure(
        go.Bar(
            x=buckets["dte_bucket"], y=buckets["mean_basis_pct"], marker_color=theme.FUTURES,
            text=[pct(value, signed=True) for value in buckets["mean_basis_pct"]], textposition="outside",
            customdata=buckets[["observations", "contracts"]],
            hovertemplate="%{x} days: %{y:.2f}%<br>%{customdata[0]} observations, %{customdata[1]} contracts<extra></extra>",
        )  # fmt: skip
    )
    figure.add_hline(y=0, line_width=1.5, line_color=theme.SPOT)
    figure.update_layout(xaxis_title="Days to expiry", yaxis=dict(title="Average basis (spot - futures), % of spot", ticksuffix="%"), hovermode="closest")
    return figure


def paths_figure(paths) -> go.Figure:
    figure = go.Figure()
    for _, contract in paths.groupby("contract_symbol"):
        contract = contract.sort_values("days_to_expiry")
        figure.add_scatter(
            x=contract["days_to_expiry"], y=contract["basis_pct"], mode="lines", line=dict(color=theme.FAINT, width=1),
            showlegend=False, hoverinfo="skip",
        )  # fmt: skip
    average = paths.groupby("days_to_expiry")["basis_pct"].mean().rolling(5, center=True, min_periods=1).mean()
    figure.add_scatter(x=average.index, y=average.values, name="Average across contracts (5-day smoothed)", line=dict(color=theme.FUTURES, width=3))
    figure.add_hline(y=0, line_width=1.5, line_color=theme.SPOT)
    figure.update_layout(yaxis=dict(title="Basis (spot - futures), % of spot", ticksuffix="%"), hovermode="closest")
    figure.update_xaxes(autorange="reversed", title="Days to expiry")
    return figure


def timing_figure(buckets) -> go.Figure:
    buckets = buckets.set_index("dte_bucket").reindex(BUCKETS_FAR_TO_NEAR).dropna(subset=["avg_net_profit"]).reset_index()
    figure = go.Figure()
    figure.add_bar(
        x=buckets["dte_bucket"], y=buckets["avg_net_profit"], name="Average net profit", marker_color=theme.CARRY,
        text=[rs(value, 0, signed=True) for value in buckets["avg_net_profit"]], textposition="outside", textfont=dict(size=12),
        customdata=buckets["profitable_pct"], hovertemplate="%{x} days: %{text} per quintal<br>paid on %{customdata:.0f}% of days<extra></extra>",
    )  # fmt: skip
    figure.add_scatter(
        x=buckets["dte_bucket"], y=-buckets["fixed_costs"], name="One-off costs (assaying, handling)", mode="lines",
        line=dict(color=theme.TBILL, width=1.5, dash="dash"),
    )  # fmt: skip
    figure.add_hline(y=0, line_width=1.5, line_color=theme.SPOT)
    figure.update_layout(xaxis_title="Days to expiry at entry", yaxis_title="Rs per quintal", hovermode="closest")
    return figure


def error_figure(settlements) -> go.Figure:
    figure = go.Figure()
    figure.add_bar(x=settlements["expiry"], y=settlements["convergence_error_pct"], name="Vs 3-day average spot", marker_color=theme.FUTURES, width=86400000 * 8)
    figure.add_scatter(
        x=settlements["expiry"], y=settlements["same_day_gap_pct"], name="Vs same-day spot", mode="markers",
        marker=dict(color=theme.SPOT, size=8, symbol="circle-open", line=dict(width=2)),
    )  # fmt: skip
    figure.add_hline(y=0, line_width=1, line_color=theme.FAINT)
    figure.update_layout(yaxis=dict(title="Final settlement gap, %", ticksuffix="%"), xaxis_title="Contract expiry", hovermode="x unified")
    return figure


def render(view: View) -> None:
    buckets, paths = view.tables["convergence_buckets"], view.tables["convergence_paths"]
    timing, settlements = view.tables["entry_timing_buckets"], view.tables["expiry_settlements"]
    if not components.has_rows(paths, "The selected date range has no contract within 60 days of expiry with open interest."):
        return
    by_bucket = buckets.set_index("dte_bucket")["mean_basis_pct"]

    # On how many expiries did the basis average above zero (futures below spot) in the last 10 days?
    final_days = paths.loc[paths["days_to_expiry"] <= 10].groupby("contract_symbol")["basis_pct"].mean()
    far, near = by_bucket.get("46-60"), by_bucket.get("6-10")
    components.chart(
        bucket_figure(buckets),
        "Does a contract keep its premium as delivery approaches?",
        f"No. Basis is spot minus futures: it averaged {pct(far, signed=True)} at 46 to 60 days (futures above spot) and {pct(near, signed=True)} at 6 to 10 days (futures below spot). "
        f"It averaged above zero inside the last 10 days on {(final_days > 0).sum()} of {len(final_days)} expiries. "
        "The carry is earned early in a contract's life, not in its final weeks.",
        key="bucket_basis",
    )

    left, right = st.columns(2)
    with left:
        components.chart(
            paths_figure(paths),
            "How consistent is that path from one expiry to the next?",
            f"Each grey line is one of {paths['contract_symbol'].nunique()} contracts on days it had open interest; the blue line is their average. "
            "Individual paths scatter by a few percent, but the drift up through zero, as futures fall from above spot to below it, is common to most.",
            key="paths",
        )
    with right:
        by_entry = timing.set_index("dte_bucket")
        fixed_costs = rs(by_entry["fixed_costs"].mean(), 0)
        if {"6-10", "46-60"} <= set(by_entry.index):
            short, long = by_entry.loc["6-10"], by_entry.loc["46-60"]
            timing_takeaway = (
                f"Entered 6 to 10 days out it averaged {rs(short['avg_net_profit'], 0, signed=True)} per quintal and paid on {short['profitable_pct']:.0f}% of days; "
                f"at 46 to 60 days, {rs(long['avg_net_profit'], 0, signed=True)} and {long['profitable_pct']:.0f}%. One-off costs of about "
                f"{fixed_costs} are paid whatever the holding period, so short trades start in a hole."
            )
        else:
            timing_takeaway = (
                f"One-off costs of about {fixed_costs} are paid whatever the holding period, so short trades start in a hole. "
                "The selected range does not cover both short and long entry windows; widen it to compare them."
            )
        components.chart(timing_figure(timing), f"When should a cash-and-carry be entered? {DAGGER}", timing_takeaway, key="entry_timing")

    if not components.has_rows(settlements, "No contract expired inside the selected date range, so there is no settlement to check."):
        return
    components.chart(
        error_figure(settlements),
        "Do contracts really settle at spot?",
        f"Across {len(settlements)} expired contracts the final settlement price was within "
        f"{pct(settlements['convergence_error_pct'].abs().mean(), decimals=3)} of the 3-day average spot on average (bars), against "
        f"{pct(settlements['same_day_gap_pct'].abs().mean())} for same-day spot (circles). The 3-day rule is inferred from this data.",
        key="convergence_error", height=300,
    )  # fmt: skip
