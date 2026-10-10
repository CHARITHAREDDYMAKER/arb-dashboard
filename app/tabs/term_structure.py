"""Tab 3: the futures curve on a chosen day, its slope over time, and the seasonal pattern."""

import plotly.graph_objects as go
import streamlit as st

import components
import theme
from formatting import long_date, pct
from loader import View


def curve_figure(day_curve) -> go.Figure:
    figure = go.Figure()
    figure.add_scatter(
        x=day_curve["expiry"], y=day_curve["pct_vs_spot"], mode="lines+markers+text", name="Futures, % above spot",
        line=dict(color=theme.FUTURES, width=2), marker=dict(size=9),
        text=[f"{price:,.0f}" for price in day_curve["settlement_price"]], textposition="top center",
        hovertemplate="%{x|%d %b %Y}: %{y:.2f}% vs spot<extra></extra>",
    )  # fmt: skip
    figure.add_hline(y=0, line_width=1.5, line_color=theme.SPOT, annotation_text="spot", annotation_position="bottom right")
    figure.update_layout(yaxis=dict(title="% above spot", ticksuffix="%"), xaxis_title="Contract expiry", hovermode="closest", showlegend=False)
    figure.update_xaxes(tickformat="%d %b %Y", tickvals=day_curve["expiry"])
    return figure


def slope_figure(daily) -> go.Figure:
    figure = go.Figure()
    figure.add_scatter(x=daily["date"], y=daily["slope_pct_per_month"], name="Curve slope", line=dict(color=theme.FUTURES, width=2))
    figure.add_hline(y=0, line_width=1, line_color=theme.SPOT)
    components.shade_runs(figure, daily["date"], daily["shape"] == "backwardation", theme.REVERSE, opacity=0.2)
    figure.update_layout(yaxis=dict(title="% per month", ticksuffix="%"), showlegend=False)
    return figure


def monthly_figure(monthly) -> go.Figure:
    figure = go.Figure(
        go.Bar(
            x=monthly["month"], y=monthly["avg_slope_pct_per_month"], marker_color=theme.FUTURES,
            text=[f"{slope:.2f}%" for slope in monthly["avg_slope_pct_per_month"]], textposition="outside",
        )  # fmt: skip
    )
    figure.update_layout(yaxis=dict(title="Average slope, % per month", ticksuffix="%"), hovermode="closest")
    figure.update_xaxes(tickformat="%b %y", dtick="M1")
    return figure


def render(view: View) -> None:
    curve, daily, monthly = (view.tables[name] for name in ("term_structure", "term_structure_daily", "term_structure_monthly"))
    if not components.has_rows(curve, "The selected date range contains no trading days."):
        return
    dates = sorted(curve["date"].unique())
    if len(dates) > 1:
        chosen = st.select_slider("Curve date", options=dates, value=dates[-1], format_func=long_date, key="curve_date")
    else:
        chosen = dates[0]  # a slider needs at least two dates
    day_curve = curve.loc[curve["date"] == chosen].sort_values("expiry")
    day = daily.loc[daily["date"] == chosen].iloc[0]

    if len(day_curve) > 1:
        shape_text = f"in {day['shape']}, sloping {pct(day['slope_pct_per_month'], signed=True)} a month from the nearest to the farthest held contract"
    else:
        shape_text = "a single held contract, so it has no slope"
    components.chart(
        curve_figure(day_curve),
        "What did the futures curve look like on this day?",
        f"On {long_date(chosen)} the curve was {shape_text}. Only contracts with open interest are shown ({len(day_curve)} of them); "
        "labels are settlement prices in Rs per quintal.",
        key="curve",
    )

    with_slope = daily.dropna(subset=["slope_pct_per_month"])
    if not components.has_rows(with_slope, "No day in the selected range has two contracts with open interest, so the curve has no slope."):
        return
    contango = (with_slope["shape"] == "contango").mean() * 100
    components.chart(
        slope_figure(daily),
        "Is the market paying to carry stock forward?",
        f"The curve sloped upward (contango) on {contango:.1f}% of days, by {pct(with_slope['slope_pct_per_month'].mean())} a month on average, "
        f"about {pct(with_slope['slope_pct_per_month'].mean() * 12, decimals=0)} a year. Orange bands mark backwardation, when nearby supply is tight.",
        key="slope", height=300,
    )  # fmt: skip

    steepest, flattest = (monthly.loc[monthly["avg_slope_pct_per_month"].agg(pick)] for pick in ("idxmax", "idxmin"))
    components.chart(
        monthly_figure(monthly),
        "How does the slope change through the year?",
        f"The curve was steepest in {steepest['month']:%B %Y} ({pct(steepest['avg_slope_pct_per_month'])} a month) and flattest in "
        f"{flattest['month']:%B %Y} ({pct(flattest['avg_slope_pct_per_month'])}). One year of data shows one cycle only, so read this as a pattern to test, not a rule.",
        key="monthly_slope", height=300,
    )  # fmt: skip
