"""Historical Backtest: what trading the signals would actually have earned, trade by trade.

All trading logic is in src/backtest.py. This module only collects the
settings, calls the engine (through the cached loader) and draws the results.
The backtest always runs on the full history, whatever the sidebar date range.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import components
import loader
import theme
from formatting import DAGGER, indian_number, long_date, pct, rs
from loader import View
from src import backtest as engine
from src.backtest import BacktestConfig
from src.metrics import QUINTALS_PER_MT, SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE

LAKH, CRORE = 1e5, 1e7
STRATEGIES = {
    "Cash-and-carry": (SIGNAL_CASH_AND_CARRY,),
    "Reverse arb (inventory)": (SIGNAL_REVERSE,),
    "Combined": (SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE),
}
THRESHOLDS = [8.0, 12.0, 15.0, 20.0]
MARGINS = [10.0, 15.0, 25.0]
EXIT_MODES = {engine.EXIT_AT_EXPIRY: "Hold to expiry and deliver", engine.EXIT_EARLY: "Close before expiry"}
COST_LABELS = {
    "funding": "Funding", "storage": "Storage", "assaying": "Assaying", "handling": f"Handling {DAGGER}", "fees": "Fees and brokerage",
    "gst": f"GST {DAGGER}", "stamp_duty": "Stamp duty", "slippage": "Slippage (assumption)", "transport": "Transport (assumption)",
}  # fmt: skip


def lakh(amount: float, signed: bool = False) -> str:
    """Rupees in lakh or crore, Indian style."""
    sign = "+" if signed and amount > 0 else ("-" if amount < 0 else "")
    amount = abs(amount)
    if amount >= CRORE:
        return f"{sign}Rs {indian_number(amount / CRORE, 2)} crore"
    return f"{sign}Rs {indian_number(amount / LAKH, 2)} lakh"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def settings_panel(view: View) -> tuple[BacktestConfig, str]:
    """The backtest controls. Returns the chosen configuration and the strategy label."""
    defaults = loader.load_backtest_defaults()
    lot_quintals = view.details["lot_size_mt"] * QUINTALS_PER_MT
    with st.expander("Backtest settings", expanded=True):
        first = st.columns([1.5, 1.1, 0.9, 1.5, 0.9])
        strategy = first[0].radio("Strategy", list(STRATEGIES), key="bt_strategy")
        choice = first[1].selectbox("Entry threshold (% a year, before funding)", [*[f"{value:.0f}%" for value in THRESHOLDS], "Custom"], index=1, key="bt_threshold")
        custom = first[2].number_input("Custom threshold %", 1.0, 100.0, float(defaults["threshold_pct"]), step=1.0, key="bt_custom", disabled=choice != "Custom")
        exit_mode = first[3].radio("Exit", list(EXIT_MODES), format_func=EXIT_MODES.get, key="bt_exit")
        exit_days = first[4].number_input("Days before expiry", 1, 30, int(defaults["exit_days_before"]), key="bt_exit_days", disabled=exit_mode != engine.EXIT_EARLY)

        second = st.columns(5)
        capital_lakh = second[0].number_input("Starting capital (Rs lakh)", 5.0, 100000.0, defaults["starting_capital_rs"] / LAKH, step=25.0, key="bt_capital")
        allocation = second[1].number_input("Max allocation per trade (%)", 1.0, 100.0, float(defaults["max_allocation_pct"]), step=5.0, key="bt_allocation")
        positions = second[2].number_input("Max simultaneous positions", 1, 20, int(defaults["max_positions"]), key="bt_positions")
        margin = second[3].number_input("Futures margin % (assumption)", 0.0, 100.0, float(defaults["margin_pct"]), step=1.0, key="bt_margin")
        inventory = second[4].number_input(f"Inventory held (lots of {view.details['lot_size_mt']} MT)", 0, 5000, int(defaults["inventory_lots"]), step=5, key="bt_inventory")

        third = st.columns(5)
        slippage = third[0].number_input("Slippage % per leg (assumption)", 0.0, 2.0, float(defaults["slippage_pct"]), step=0.05, key="bt_slippage")
        transport = third[1].number_input("Transport Rs/quintal (assumption)", 0.0, 500.0, float(defaults["transport_rs_per_quintal"]), step=5.0, key="bt_transport")
        third[2].markdown(
            f'<p class="legend-note">Commodity: <b>{view.name}</b> (sidebar). One lot = {lot_quintals:.0f} quintals. '
            f"Contracts need {defaults['min_dte']}+ days to expiry; size is capped at {defaults['volume_cap_pct']:.0f}% of 20-day average volume; "
            f"a {defaults['buffer_pct']:.0f}% cash buffer is held (assumption).</p>",
            unsafe_allow_html=True,
        )
    config = BacktestConfig(
        threshold_pct=custom if choice == "Custom" else float(choice.rstrip("%")),
        min_dte=int(defaults["min_dte"]),
        exit_mode=exit_mode,
        exit_days_before=int(exit_days),
        starting_capital=capital_lakh * LAKH,
        max_allocation_pct=allocation,
        max_positions=int(positions),
        margin_pct=margin,
        buffer_pct=float(defaults["buffer_pct"]),
        volume_cap_pct=float(defaults["volume_cap_pct"]),
        inventory_lots=int(inventory),
        slippage_pct=slippage,
        transport_rs_per_quintal=transport,
        sides=STRATEGIES[strategy],
    )
    return config, strategy


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #


def trades_on_price_figure(view: View, trades: pd.DataFrame) -> go.Figure:
    """Spot and the active futures with every entry and exit marked."""
    economics = view.full["economics"]
    figure = go.Figure()
    figure.add_scatter(x=economics["date"], y=economics["spot"], name="Spot", line=dict(color=theme.SPOT, width=1.8))
    figure.add_scatter(x=economics["date"], y=economics["settlement_price"], name="Active futures", line=dict(color=theme.FUTURES, width=1.5))
    styles = {SIGNAL_CASH_AND_CARRY: ("Cash-and-carry entry", theme.CARRY, "triangle-up"), SIGNAL_REVERSE: ("Reverse-arb entry", theme.REVERSE, "triangle-down")}
    for side, (label, colour, symbol) in styles.items():
        own = trades.loc[trades["side"] == side] if not trades.empty else trades
        if own.empty:
            continue
        figure.add_scatter(
            x=own["entry_date"], y=own["entry_spot"], mode="markers", name=label,
            marker=dict(symbol=symbol, size=13, color=colour, line=dict(color=theme.PAGE, width=1.5)),
            customdata=own[["contract_symbol", "lots", "quoted_yield_pct", "entry_futures"]],
            hovertemplate="%{x|%d %b %Y}<br>%{customdata[0]}, %{customdata[1]} lots<br>spot %{y:,.0f}, futures %{customdata[3]:,.0f}<br>quoted %{customdata[2]:.1f}% a year<extra>entry</extra>",
        )  # fmt: skip
    if not trades.empty:
        figure.add_scatter(
            x=trades["exit_date"], y=trades["exit_futures"], mode="markers", name="Exit (final settlement)",
            marker=dict(symbol="x", size=9, color=theme.INK, line=dict(width=1)),
            customdata=trades[["contract_symbol", "net_pnl", "holding_days"]],
            hovertemplate="%{x|%d %b %Y}<br>%{customdata[0]}<br>settled %{y:,.0f}<br>net Rs %{customdata[1]:,.0f} after %{customdata[2]} days<extra>exit</extra>",
        )  # fmt: skip
    figure.update_layout(yaxis_title="Rs per quintal", hovermode="closest", legend=dict(font=dict(size=11)))
    return figure


def equity_figure(equity: pd.DataFrame, base: float, starting_capital: float) -> go.Figure:
    """Equity on top, capital in use below: two panels with their own scales."""
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, row_heights=[0.6, 0.4], subplot_titles=("Equity (Rs lakh)", "Capital in use (Rs lakh)"))
    level = (equity["equity"] - starting_capital + base) / LAKH
    figure.add_scatter(x=equity["date"], y=level, name="Equity, marked to market", line=dict(color=theme.FUTURES, width=2), row=1, col=1)
    figure.add_scatter(
        x=equity["date"], y=equity["capital_deployed"] / LAKH, name="Cash deployed", line=dict(color=theme.CARRY, width=1),
        fill="tozeroy", fillcolor=theme.hex_to_rgba(theme.CARRY, 0.3), row=2, col=1,
    )  # fmt: skip
    if equity["inventory_released"].any():
        figure.add_scatter(
            x=equity["date"], y=equity["inventory_released"] / LAKH, name="Inventory value released", line=dict(color=theme.REVERSE, width=1),
            fill="tozeroy", fillcolor=theme.hex_to_rgba(theme.REVERSE, 0.25), row=2, col=1,
        )  # fmt: skip
    figure.add_hline(y=base / LAKH, line_width=1, line_dash="dash", line_color=theme.TBILL, row=1, col=1)
    figure.update_annotations(font=dict(size=12, color=theme.MUTED), xanchor="left", x=0)
    figure.update_layout(legend=dict(y=1.08, font=dict(size=11)))
    return figure


def cumulative_figure(equity: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    figure.add_scatter(x=equity["date"], y=equity["realised_pnl"] / LAKH, name="Realised net P&L", line=dict(color=theme.CARRY, width=2, shape="hv"))
    figure.add_hline(y=0, line_width=1, line_color=theme.FAINT)
    figure.update_layout(yaxis_title="Rs lakh", showlegend=False)
    return figure


def monthly_figure(trades: pd.DataFrame) -> go.Figure:
    monthly = trades.assign(month=trades["exit_date"].dt.to_period("M").dt.to_timestamp()).groupby("month")["net_pnl"].sum() / LAKH
    figure = go.Figure(
        go.Bar(
            x=monthly.index, y=monthly.values, marker_color=theme.FUTURES,
            text=[f"{value:+.2f}" for value in monthly.values], textposition="outside", cliponaxis=False,
        )  # fmt: skip
    )
    figure.add_hline(y=0, line_width=1, line_color=theme.FAINT)
    figure.update_layout(yaxis_title="Net P&L by exit month, Rs lakh", hovermode="closest")
    figure.update_xaxes(tickformat="%b %y", dtick="M1")
    return figure


def cost_figure(costs_by_line: dict) -> go.Figure:
    lines = {COST_LABELS[name]: value for name, value in costs_by_line.items() if value > 0}
    ordered = dict(sorted(lines.items(), key=lambda item: item[1]))
    figure = go.Figure(
        go.Bar(
            y=list(ordered), x=[value / LAKH for value in ordered.values()], orientation="h", marker_color=theme.TBILL,
            text=[f"{value / LAKH:.2f}" for value in ordered.values()], textposition="outside", cliponaxis=False,
        )  # fmt: skip
    )
    figure.update_layout(xaxis_title="Rs lakh", hovermode="closest", margin=dict(r=50))
    return figure


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

TRADE_COLUMNS = {
    "entry_date": st.column_config.DateColumn("Entry", format="DD MMM YYYY", pinned=True, width=92),
    "contract_symbol": st.column_config.TextColumn("Contract", pinned=True, width=150),
    "side": st.column_config.TextColumn("Side", width=100),
    "net_pnl": st.column_config.NumberColumn("Net P&L Rs", format="%.0f", width=90),
    "annualised_return_pct": st.column_config.NumberColumn("Realised % a year (after funding)", format="%.2f", width=115),
    "annualised_before_funding_pct": st.column_config.NumberColumn("Realised % a year (before funding)", format="%.2f", width=115),
    "quoted_yield_pct": st.column_config.NumberColumn("Quoted % at entry (before funding)", format="%.2f", width=110),
    "exit_date": st.column_config.DateColumn("Exit", format="DD MMM YYYY", width=92),
    "holding_days": st.column_config.NumberColumn("Days held", width=70),
    "lots": st.column_config.NumberColumn("Lots", width=55),
    "capital_used": st.column_config.NumberColumn("Capital used Rs", format="%.0f", width=105),
    "entry_spot": st.column_config.NumberColumn("Entry spot (polled)", format="%.2f", width=95),
    "entry_futures": st.column_config.NumberColumn("Entry futures (settlement)", format="%.2f", width=105),
    "exit_futures": st.column_config.NumberColumn("Exit futures (settlement)", format="%.2f", width=105),
    "exit_type": st.column_config.TextColumn("Exit type", width=170),
    "gross_pnl": st.column_config.NumberColumn("Gross Rs", format="%.0f", width=80),
    "saved_storage": st.column_config.NumberColumn("Storage saved Rs", format="%.0f", width=95),
    "saved_interest": st.column_config.NumberColumn("Interest saved Rs", format="%.0f", width=95),
    "cost_funding": st.column_config.NumberColumn("Funding Rs", format="%.0f", width=80),
    "cost_storage": st.column_config.NumberColumn("Storage Rs", format="%.0f", width=80),
    "cost_assaying": st.column_config.NumberColumn("Assaying Rs", format="%.0f", width=80),
    "cost_handling": st.column_config.NumberColumn("Handling Rs", format="%.0f", width=80),
    "cost_fees": st.column_config.NumberColumn("Fees Rs", format="%.0f", width=70),
    "cost_gst": st.column_config.NumberColumn("GST Rs", format="%.0f", width=70),
    "cost_stamp_duty": st.column_config.NumberColumn("Stamp duty Rs", format="%.0f", width=85),
    "cost_slippage": st.column_config.NumberColumn("Slippage Rs", format="%.0f", width=80),
    "cost_transport": st.column_config.NumberColumn("Transport Rs", format="%.0f", width=85),
    "total_costs": st.column_config.NumberColumn("Total costs Rs", format="%.0f", width=95),
    "price_source": st.column_config.TextColumn("Prices", width=330),
}
SIDE_NAMES = {SIGNAL_CASH_AND_CARRY: "Cash-and-carry", SIGNAL_REVERSE: "Reverse arb"}


def comparison_table(view: View, config: BacktestConfig) -> pd.DataFrame:
    """The same settings run three ways: cash only, inventory only, and both."""
    rows = []
    for label, sides in STRATEGIES.items():
        summary = loader.run_backtest(view, engine.BacktestConfig(**{**config.__dict__, "sides": sides}))["summary"]
        rows.append(
            {
                "Strategy": label,
                "Opportunities": summary["independent_opportunities"],
                "Trades": summary["trades_executed"],
                "Capital base": lakh(summary["capital_base"]),
                "Peak in use": lakh(summary["peak_capital_deployed"]),
                "Net profit": lakh(summary["total_net_profit"], signed=True),
                "Return a year, before funding": pct(summary["annualised_return_on_total_before_funding_pct"]),
                "Return a year, after funding": pct(summary["annualised_return_on_total_pct"]),
                "On capital in use, before funding": pct(summary["return_on_deployed_before_funding_pct"]),
                "Max drawdown": pct(summary["max_drawdown_pct"]),
            }
        )
    return pd.DataFrame(rows)


def sensitivity_table(view: View, config: BacktestConfig) -> pd.DataFrame:
    """Threshold down the side, margin across the top: trades, net profit, and annualised return on total capital."""
    grid = {}
    for margin in MARGINS:
        column = []
        for threshold in THRESHOLDS:
            summary = loader.run_backtest(view, engine.BacktestConfig(**{**config.__dict__, "threshold_pct": threshold, "margin_pct": margin}))["summary"]
            column.append(
                f"{summary['trades_executed']} trades · {lakh(summary['total_net_profit'], signed=True)} · "
                f"{pct(summary['annualised_return_on_total_before_funding_pct'])} a year"
            )
        grid[f"Margin {margin:.0f}%"] = column
    return pd.DataFrame(grid, index=[f"Threshold {threshold:.0f}%" for threshold in THRESHOLDS]).rename_axis("Entry threshold").reset_index()


# --------------------------------------------------------------------------- #
# Tab
# --------------------------------------------------------------------------- #


def render(view: View) -> None:
    config, strategy = settings_panel(view)
    result = loader.run_backtest(view, config)
    trades, equity, summary = result["trades"], result["equity"], result["summary"]
    period = f"{long_date(equity['date'].iloc[0])} to {long_date(equity['date'].iloc[-1])}"

    st.markdown(
        f'<p class="legend-note">Backtest of <b>{strategy.lower()}</b> on {view.name.lower()}, {period} (full history; the sidebar date range does not apply). '
        "<b>Actual:</b> futures entry, exit and final settlement prices, polled spot, volume, open interest. "
        "<b>Modelled:</b> buying and selling physical goods at the polled spot price, trading at the close, every cost line, margin and buffer. "
        f"Each day uses only data known that day. Figures marked {DAGGER} depend on the handling and GST assumptions.</p>",
        unsafe_allow_html=True,
    )

    wins, losses = summary["wins"], summary["losses"]
    open_note = f"{summary['trades_still_open']} more still open" if summary["trades_still_open"] else "none still open"
    components.kpi_row(
        [
            dict(label="Trading days", value=f"{summary['trading_days']}", sub=period),
            dict(label="Opportunity days", value=f"{summary['opportunity_days']}", sub=f"days with a signal at or above {config.threshold_pct:.0f}%"),
            dict(label="Independent opportunities", value=f"{summary['independent_opportunities']}", sub="runs of consecutive signal days on one contract"),
            dict(label="Trades executed", value=f"{summary['trades_executed']}", sub=f"completed; {open_note}"),
            dict(label="Win / loss", value=f"{wins} / {losses}", sub="trades with net profit above / at or below zero"),
        ]
    )
    components.kpi_row(
        [
            dict(label=f"Total net profit {DAGGER}", value=lakh(summary["total_net_profit"], signed=True), sub=f"after funding; {lakh(summary['net_profit_before_funding'], signed=True)} before funding"),
            dict(
                label=f"Portfolio return a year {DAGGER}",
                value=pct(summary["annualised_return_on_total_before_funding_pct"]),
                sub=f"before funding, on {lakh(summary['capital_base'])}; {pct(summary['annualised_return_on_total_pct'])} after funding",
            ),
            dict(label="Max drawdown", value=pct(summary["max_drawdown_pct"]), sub=f"{lakh(summary['max_drawdown_rs'])}, marked to market daily"),
            dict(label="Capital utilisation", value=pct(summary["capital_utilisation_pct"]), sub=f"peak in use {lakh(summary['peak_capital_deployed'])}"),
            dict(label=f"Total costs {DAGGER}", value=lakh(summary["total_costs"]), sub=f"of {lakh(summary['total_gross_profit'], signed=True)} gross"),
        ]
    )
    if trades.empty:
        st.info("No trade was completed with these settings. Lower the entry threshold, add capital or inventory, or check that the strategy has stock to sell.")
        return

    # The contrast: what a signal quotes against what the capital actually earned.
    quoted, actual = summary["avg_quoted_yield_pct"], summary["annualised_return_on_total_before_funding_pct"]
    in_use = summary["return_on_deployed_before_funding_pct"]
    capital_per_rupee = (trades["capital_used"] / (trades["entry_spot"] * trades["quantity"])).mean()
    not_traded = summary["opportunities_not_traded"]
    st.markdown('<p class="question">Why is the return on capital so far below the quoted yield?</p>', unsafe_allow_html=True)
    left, right = st.columns(2)
    with left:
        components.kpi(label="Average quoted opportunity yield (before funding)", value=pct(quoted), sub="annualised on spot value, averaged over every signal day")
    with right:
        components.kpi(label=f"Actual annualised return on capital (before funding) {DAGGER}", value=pct(actual), sub=f"net profit over the whole capital base for the year; {pct(in_use)} on capital while in use")
    st.markdown(
        f'<p class="takeaway">The quoted yield annualises a gain that lasts a few weeks, as if it could be earned all year. It cannot. '
        f"Capital was in use only {pct(summary['capital_utilisation_pct'])} of the time; each rupee of goods needed Rs {capital_per_rupee:.2f} of capital "
        f"(margin, buffer and one-off costs); and {not_traded} of {summary['independent_opportunities']} opportunities were not traded "
        f"(contract already traded, too thin, or no free capital). While it was working, capital earned {pct(in_use)} a year before funding.</p>",
        unsafe_allow_html=True,
    )

    components.chart(
        trades_on_price_figure(view, trades),
        "When were trades entered and closed?",
        f"{summary['trades_executed']} completed trades. Triangles mark entries at that day's spot; crosses mark exits at the contract's settlement price. "
        f"Average holding period {summary['avg_holding_days']:.0f} days. Futures prices are actual settlements; the spot price is the polled reference price, assumed tradable.",
        key="bt_price", height=380,
    )  # fmt: skip
    left, right = st.columns(2)
    with left:
        components.chart(
            equity_figure(equity, summary["capital_base"], config.starting_capital),
            "How did equity move, and how much capital was working?",
            f"Equity is marked to market at each day's settlement and spot, so it dips while a trade is open even though the delivered profit is locked. "
            f"Largest fall from a peak: {pct(summary['max_drawdown_pct'])}. Peak capital in use: {lakh(summary['peak_capital_deployed'])}.",
            key="bt_equity", height=420,
        )  # fmt: skip
    with right:
        components.chart(
            cumulative_figure(equity),
            f"How did realised profit build up? {DAGGER}",
            f"Profit is booked when a trade closes. Total {lakh(summary['total_net_profit'], signed=True)} after funding from {summary['trades_executed']} trades, "
            f"an average of {rs(summary['total_net_profit'] / summary['trades_executed'], 0, signed=True)} a trade.",
            key="bt_cumulative", height=420,
        )  # fmt: skip
    left, right = st.columns(2)
    with left:
        components.chart(
            monthly_figure(trades),
            f"In which months was the profit booked? {DAGGER}",
            "Net P&L by the month each trade closed. Months with no bar had no trade expiring.",
            key="bt_monthly", height=320,
        )  # fmt: skip
    with right:
        biggest = max(summary["costs_by_line"].items(), key=lambda item: item[1])
        components.chart(
            cost_figure(summary["costs_by_line"]),
            f"Where did the costs go? {DAGGER}",
            f"Costs took {lakh(summary['total_costs'])} of {lakh(summary['total_gross_profit'], signed=True)} gross. The largest was "
            f"{COST_LABELS[biggest[0]].replace(' ' + DAGGER, '').lower()} at {lakh(biggest[1])}. Funding is charged on capital used, storage for days held.",
            key="bt_costs", height=320,
        )  # fmt: skip

    st.markdown(f'<p class="question">Trade history {DAGGER}</p>', unsafe_allow_html=True)
    table = trades.assign(side=trades["side"].map(SIDE_NAMES)).sort_values("entry_date")
    st.dataframe(table[list(TRADE_COLUMNS)], column_config=TRADE_COLUMNS, hide_index=True, height=min(len(table) + 1, 13) * 35 + 3)
    st.download_button("Download trade history (CSV)", table.to_csv(index=False), file_name=f"{view.commodity.lower()}_backtest_trades.csv", mime="text/csv", key="bt_download")

    st.markdown(f'<p class="question">Cash-and-carry, reverse arb, or both? {DAGGER}</p>', unsafe_allow_html=True)
    st.dataframe(comparison_table(view, config), hide_index=True)
    st.markdown(
        '<p class="takeaway">Cash-and-carry is measured on the starting cash. Reverse arb is measured on the value of the inventory held, because it needs stock, not cash. '
        "Combined uses both bases. Only one position is allowed in a contract at a time, so in the combined run a cash-and-carry held to expiry "
        "blocks a later reverse trade in the same contract; that is why combined can earn less than reverse arb alone.</p>",
        unsafe_allow_html=True,
    )

    st.markdown(f'<p class="question">How sensitive is the result to the threshold and the margin? {DAGGER}</p>', unsafe_allow_html=True)
    st.dataframe(sensitivity_table(view, config), hide_index=True)
    st.caption(
        "Each cell: completed trades, net profit after funding, and annualised return on total capital before funding. "
        "A higher threshold takes fewer, better trades. A higher margin ties up more cash per lot. Margin does not affect reverse arb, which uses inventory."
    )
