"""Tab 5: what the roll costs, how fast positions move, and how much size the market takes."""

import plotly.graph_objects as go
import streamlit as st

import components
import theme
from formatting import long_date, pct, rs, rs_crore
from loader import View


def by_day(roll, column: str):
    """Average of `column` by trading days left, ordered towards expiry (4, 3, 2, 1, 0)."""
    return roll.groupby("trading_days_to_expiry")[column].mean().sort_index(ascending=False)


def day_label(days_left: int) -> str:
    return "expiry day" if days_left == 0 else str(days_left)


def spread_figure(roll, column: str, title: str, suffix: str = "") -> go.Figure:
    average = by_day(roll, column)
    figure = go.Figure(
        go.Bar(
            x=[day_label(day) for day in average.index], y=average.values, marker_color=theme.FUTURES,
            text=[f"{value:,.1f}{suffix}" for value in average.values], textposition="outside",
        )  # fmt: skip
    )
    figure.update_layout(xaxis=dict(title="Trading days before expiry", type="category"), yaxis=dict(title=title, ticksuffix=suffix), hovermode="closest")
    return figure


def roll_figure(roll) -> go.Figure:
    figure = go.Figure()
    for _, expiry in roll.groupby("contract_symbol_near"):
        figure.add_scatter(
            x=expiry["trading_days_to_expiry"], y=expiry["roll_pct"], mode="lines", line=dict(color=theme.FAINT, width=1),
            showlegend=False, hoverinfo="skip",
        )  # fmt: skip
    average = by_day(roll, "roll_pct")
    figure.add_scatter(
        x=average.index, y=average.values, name="Average across expiries", mode="lines+markers+text",
        line=dict(color=theme.FUTURES, width=3), text=[f"{value:.0f}%" for value in average.values], textposition="top center",
    )  # fmt: skip
    figure.update_layout(yaxis=dict(title="Next-month share of open interest", ticksuffix="%", range=[max(0, roll["roll_pct"].min() - 10), 106]), hovermode="closest")
    figure.update_xaxes(autorange="reversed", dtick=1, title="Trading days before expiry")
    return figure


def capacity_figure(liquidity) -> go.Figure:
    median = liquidity["capacity_rs_crore"].median()
    figure = go.Figure()
    figure.add_scatter(x=liquidity["date"], y=liquidity["capacity_rs_crore"], name="Deployable per day", line=dict(color=theme.FUTURES, width=2))
    figure.add_hline(y=median, line_width=1.5, line_dash="dash", line_color=theme.TBILL, annotation_text=f"median {rs_crore(median)}", annotation_position="top left")
    figure.update_layout(yaxis=dict(title="Rs crore per day", rangemode="tozero"), showlegend=False)
    return figure


def render_roll(view: View, roll) -> None:
    """The three roll charts. They need at least one expiry week in the date range."""
    spread, spread_ann = roll["calendar_spread"].mean(), roll["calendar_spread_ann_pct"].mean()
    expiries = roll["contract_symbol_near"].nunique()

    left, right = st.columns(2)
    with left:
        components.chart(
            spread_figure(roll, "calendar_spread", "Next minus near, Rs per quintal"),
            "What does it cost to roll into the next month?",
            f"In the last five trading days before expiry the next contract traded {rs(spread, 0)} per quintal above the expiring one on average, "
            f"across {expiries} expiries. A long pays this to roll; a stock holder earns it by selling the next month.",
            key="spread_rs", height=320,
        )  # fmt: skip
    with right:
        components.chart(
            spread_figure(roll, "calendar_spread_ann_pct", "Annualised, % a year", "%"),
            "How does that spread compare with the cost of money?",
            f"Annualised over the gap between the two expiries, the spread averaged {pct(spread_ann)} a year, "
            f"against a T-bill yield near {pct(view.tables['economics']['tbill_pct'].mean())}. The curve pays well above financing to carry stock one more month.",
            key="spread_ann", height=320,
        )  # fmt: skip

    roll_by_day = by_day(roll, "roll_pct")
    components.chart(
        roll_figure(roll),
        "How early do positions leave the expiring contract?",
        f"Five trading days before expiry the next month already held {roll_by_day.iloc[0]:.0f}% of the two contracts' open interest, "
        f"rising to {roll_by_day.iloc[-1]:.0f}% on expiry day. Liquidity has moved on before expiry week begins, which is why the active contract rolls early.",
        key="roll_pct", height=320,
    )  # fmt: skip


def render(view: View) -> None:
    roll, liquidity = view.tables["rollover"], view.tables["liquidity"]
    if not components.has_rows(liquidity, "The selected date range contains no trading days."):
        return
    if components.has_rows(roll, "The selected date range contains no expiry week, so there is no roll to measure."):
        render_roll(view, roll)

    latest, lot = view.full["liquidity"].iloc[-1], view.details["lot_size_mt"]
    as_of = long_date(latest["date"])
    share = view.rules.capacity_share_of_volume * 100
    components.chart(
        capacity_figure(liquidity),
        "How much size can actually be traded?",
        f"At {share:.0f}% of {view.rules.capacity_window_days}-day average volume the active contract took about {rs_crore(latest['capacity_rs_crore'])} a day on {as_of} "
        f"({latest['capacity_lots']:,.0f} lots of {lot} MT), against a median of {rs_crore(liquidity['capacity_rs_crore'].median())}. "
        "This, not the return, is what caps the strategy.",
        key="capacity", height=300,
    )  # fmt: skip
    components.kpi_row(
        [
            dict(label=f"Volume, {as_of}", value=f"{latest['volume_lots']:,.0f} lots", sub=rs_crore(latest["volume_rs_crore"])),
            dict(label="Open interest", value=f"{latest['open_interest_lots']:,.0f} lots", sub=rs_crore(latest["open_interest_rs_crore"])),
            dict(label="Capacity", value=rs_crore(latest["capacity_rs_crore"]), sub=f"{latest['capacity_lots']:,.0f} lots a day"),
            dict(label="Capacity, median", value=rs_crore(liquidity["capacity_rs_crore"].median()), sub=f"lowest {rs_crore(liquidity['capacity_rs_crore'].min())}"),
        ]
    )
