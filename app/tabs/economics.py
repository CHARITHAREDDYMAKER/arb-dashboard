"""Tab 2: what each side of the trade earns after costs, and where the money goes."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import components
import theme
from formatting import DAGGER, long_date, pct, rs
from loader import SHORT_DATED_DAYS, View
from src.metrics import QUINTALS_PER_MT, SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE

RETURN_AXIS = [-60, 90]  # annualised returns are clipped to this range so a few short-dated days do not flatten the chart


def return_figure(economics: pd.DataFrame, column: str, signal: str, colour: str, label: str) -> go.Figure:
    """Annualised return of one side against the T-bill, shaded where that side is tradeable."""
    long_dated = economics[column].where(economics["days_to_expiry"] >= SHORT_DATED_DAYS)
    figure = go.Figure()
    figure.add_scatter(x=economics["date"], y=economics[column], name=f"Short-dated (under {SHORT_DATED_DAYS} days)", line=dict(color=theme.FAINT, width=1.5))
    figure.add_scatter(x=economics["date"], y=long_dated, name=label, line=dict(color=colour, width=2), connectgaps=False)
    figure.add_scatter(x=economics["date"], y=economics["tbill_pct"], name="91-day T-bill", line=dict(color=theme.TBILL, width=1.5, dash="dash"))
    components.shade_runs(figure, economics["date"], economics["tradeable_signal"] == signal, colour)
    figure.update_layout(yaxis=dict(range=RETURN_AXIS, title="% a year", ticksuffix="%"), legend=dict(font=dict(size=11)))
    return figure


def waterfall_figure(view: View) -> go.Figure:
    """The latest day's cash-and-carry per quintal: gross basis, each cost, and what is left."""
    latest, costs = view.latest, view.costs
    gst_multiplier = costs.warehouse_gst_multiplier
    steps = {
        "Gross": latest["cc_gross"],
        "Funding": -latest["cc_funding"],
        "Storage": -latest["cc_storage"] / gst_multiplier,
        "Assay": -latest["cc_assaying"] / gst_multiplier,
        f"Handling{DAGGER}": -latest["cc_handling"],
        "Fees": -latest["cc_fees_ex_gst"],
        f"GST{DAGGER}": -latest["cc_gst"],
    }
    values = [*steps.values(), latest["cc_net_profit"]]
    figure = go.Figure(
        go.Waterfall(
            x=[*steps, "Net"],
            y=[*steps.values(), 0],
            measure=[*["relative"] * len(steps), "total"],
            text=[f"{value:+,.0f}" if abs(value) >= 100 else f"{value:+,.1f}" for value in values],
            textposition="outside",
            textfont=dict(size=12, color=theme.INK),
            constraintext="none",
            cliponaxis=False,
            connector=dict(line=dict(color=theme.FAINT, width=1)),
            increasing=dict(marker_color=theme.FUTURES),
            decreasing=dict(marker_color=theme.TBILL),
            totals=dict(marker_color=theme.CARRY if latest["cc_net_profit"] > 0 else theme.SPOT),
        )
    )
    # Leave headroom so the labels above and below the bars are never cut off.
    running = pd.Series(list(steps.values())).cumsum()
    low, high = min(0, running.min()), max(0, running.max())
    padding = (high - low) * 0.15 or 1
    figure.update_layout(yaxis=dict(title="Rs per quintal", range=[low - padding, high + padding]), hovermode="closest", showlegend=False)
    figure.update_xaxes(tickangle=0, tickfont=dict(size=11))
    figure.update_yaxes(zeroline=True, zerolinewidth=1)
    return figure


def break_even_figure(economics: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    figure.add_scatter(x=economics["date"], y=economics["break_even_storage"], name="Break-even storage", line=dict(color=theme.CARRY, width=2))
    figure.add_scatter(x=economics["date"], y=economics["actual_storage"], name="Actual storage (NCCL)", line=dict(color=theme.SPOT, width=1.5, dash="dash"))
    components.shade_runs(figure, economics["date"], economics["break_even_storage"] > economics["actual_storage"], theme.CARRY)
    figure.update_layout(yaxis=dict(range=[-15, 25], title="Rs per quintal per day"))
    return figure


def side_summary(view: View, signal: str, title: str) -> dict:
    summary = view.tables["opportunity_summary"]
    row = summary.loc[summary["signal"] == signal]
    if row.empty:
        return dict(label=f"{title} {DAGGER}", value="0 days", sub="no tradeable signal in the period")
    row = row.iloc[0]
    return dict(
        label=f"{title} {DAGGER}",
        value=f"{int(row['signal_days'])} days",
        sub=f"{pct(row['share_of_days_pct'])} of days · median {pct(row['median_locked_return_ann_pct'])} p.a. · "
        f"average {rs(row['avg_locked_profit'])} per quintal",
    )


def gst_card(row: dict) -> dict:
    title = "With 18% GST" if row["gst_applied"] else "Without GST"
    current = " (current setting)" if row["is_current"] else ""
    return dict(
        label=f"{title}{current}",
        value=f"{pct(row['carry_days_pct'])} <span class='unit'>of days cash-and-carry tradeable</span>",
        sub=f"reverse arb tradeable on {pct(row['reverse_days_pct'])} of days",
    )


def render(view: View) -> None:
    economics, log = view.tables["economics"], view.tables["opportunities"]
    if not components.has_rows(economics, "The selected date range contains no trading days."):
        return
    latest, as_of = view.latest, long_date(view.latest["date"])
    carry_days = economics["tradeable_signal"] == SIGNAL_CASH_AND_CARRY
    reverse_days = economics["tradeable_signal"] == SIGNAL_REVERSE
    clip_note = f"Axis clipped at {RETURN_AXIS[0]}% and {RETURN_AXIS[1]}%; grey = {components.SHORT_DATED_NOTE}."

    left, right = st.columns(2)
    with left:
        components.chart(
            return_figure(economics, "cc_carry_return_pct", SIGNAL_CASH_AND_CARRY, theme.CARRY, "Cash-and-carry return"),
            f"Cash-and-carry: does buying spot and selling futures beat the T-bill? {DAGGER}",
            f"Tradeable on {carry_days.sum()} of {len(economics)} days (shaded). On those days the median locked return was "
            f"{pct(economics.loc[carry_days, 'cc_carry_return_pct'].median())} a year against a T-bill near {pct(economics['tbill_pct'].mean())}. {clip_note}",
            key="carry_return",
        )
    with right:
        components.chart(
            return_figure(economics, "rev_return_pct", SIGNAL_REVERSE, theme.REVERSE, "Reverse-arb return"),
            f"Reverse arb: what does a stock holder gain by selling spot and buying futures? {DAGGER}",
            f"Tradeable on {reverse_days.sum()} of {len(economics)} days (shaded), median gain "
            f"{pct(economics.loc[reverse_days, 'rev_return_pct'].median())} a year. It needs existing stock: physical goods cannot be shorted, "
            f"which is why the gap persists. {clip_note}",
            key="reverse_return",
        )

    lot_quintals = view.details["lot_size_mt"] * QUINTALS_PER_MT
    costs_total = latest["cc_total_costs"]
    gst = view.costs.warehouse_gst_multiplier
    biggest = max(
        {"funding": latest["cc_funding"], "storage": latest["cc_storage"] / gst, "assaying": latest["cc_assaying"] / gst, "handling": latest["cc_handling"]}.items(),
        key=lambda item: item[1],
    )
    left, right = st.columns(2)
    with left:
        components.chart(
            waterfall_figure(view),
            f"Where does the basis go on the latest day? {DAGGER}",
            f"On {as_of}: of {rs(latest['cc_gross'], signed=True)} gross basis per quintal, costs take {rs(costs_total)}; the largest is {biggest[0]} at {rs(biggest[1])} before GST. "
            f"Net {rs(latest['cc_net_profit'], signed=True)} per quintal, or {rs(latest['cc_net_profit'] * lot_quintals, 0, signed=True)} per "
            f"{view.details['lot_size_mt']} MT lot, held {latest['days_to_expiry']} days to delivery.",
            key="waterfall",
        )
    with right:
        covered = (economics["break_even_storage"] > economics["actual_storage"]).mean() * 100
        components.chart(
            break_even_figure(economics),
            f"How much storage cost could the trade bear? {DAGGER}",
            f"Break-even storage was above the actual {rs(latest['actual_storage'])} per quintal per day on {covered:.0f}% of days (shaded). "
            f"On {as_of} it was {rs(latest['break_even_storage'])}. Below zero means futures do not even cover funding and one-off costs. Axis clipped at -15 and 25.",
            key="break_even",
        )

    st.markdown(f'<p class="question">When was each side tradeable? {DAGGER}</p>', unsafe_allow_html=True)
    components.kpi_row(
        [side_summary(view, SIGNAL_CASH_AND_CARRY, "Cash-and-carry"), side_summary(view, SIGNAL_REVERSE, "Reverse arb")]
    )
    st.markdown(f'<p class="question">What if GST does not apply to storage and assaying? {DAGGER}</p>', unsafe_allow_html=True)
    components.kpi_row([gst_card(row) for row in view.gst_what_if.to_dict("records")])
    carry_share = dict(zip(view.gst_what_if["gst_applied"], view.gst_what_if["carry_days_pct"]))
    with_gst, without_gst = carry_share[True], carry_share[False]
    st.markdown(
        f'<p class="takeaway">Dropping the 18% GST lifts tradeable cash-and-carry days from {pct(with_gst)} to {pct(without_gst)} of the year, '
        f"a change of {without_gst - with_gst:+.2f} points. The unverified GST treatment barely moves the conclusion; "
        "the basis and the one-off costs matter far more.</p>",
        unsafe_allow_html=True,
    )

    st.markdown(f'<p class="question">Every tradeable day {DAGGER}</p>', unsafe_allow_html=True)
    table = log.assign(
        signal=log["signal"].map({SIGNAL_CASH_AND_CARRY: "Cash-and-carry", SIGNAL_REVERSE: "Reverse arb"}),
        short_dated=np.where(log["days_to_expiry"] < SHORT_DATED_DAYS, "yes", ""),
    ).sort_values("entry_date", ascending=False)
    # The first four columns are pinned so they stay in view on a laptop screen.
    columns = {
        "entry_date": st.column_config.DateColumn("Entry date", format="DD MMM YYYY", pinned=True, width=95),
        "contract_symbol": st.column_config.TextColumn("Contract", pinned=True, width=165),
        "signal": st.column_config.TextColumn("Side", pinned=True, width=105),
        "locked_profit": st.column_config.NumberColumn("Net profit Rs/qtl", format="%.2f", pinned=True, width=110),
        "locked_return_ann_pct": st.column_config.NumberColumn("Net return % p.a.", format="%.2f", width=110),
        "days_to_expiry": st.column_config.NumberColumn("Days to expiry", width=95),
        "short_dated": st.column_config.TextColumn("Short-dated", width=85),
        "spot": st.column_config.NumberColumn("Spot", format="%.2f", width=85),
        "settlement_price": st.column_config.NumberColumn("Futures", format="%.2f", width=85),
        "mispricing_pct": st.column_config.NumberColumn("Vs fair value %", format="%.2f", width=100),
        "convergence_error_pct": st.column_config.NumberColumn("Convergence error %", format="%.3f", width=130),
    }
    st.dataframe(table[list(columns)], column_config=columns, hide_index=True, height=320)
    st.download_button(
        "Download opportunity log (CSV)", table[list(columns)].to_csv(index=False), file_name=f"{view.commodity.lower()}_opportunities.csv", mime="text/csv"
    )
    st.markdown(
        '<p class="takeaway">Each row is a day the trade made money after all costs if entered at the close and held to expiry. '
        "Convergence error is how far the contract finally settled from the 3-day average spot (blank until it expires). "
        f"Short-dated rows: {components.SHORT_DATED_NOTE}.</p>",
        unsafe_allow_html=True,
    )
