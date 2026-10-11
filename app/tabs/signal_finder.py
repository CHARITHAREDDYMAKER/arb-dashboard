"""Signal Finder: both trades worked through for any date, the best dates of the year, and why they happened."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import components
import theme
from formatting import DAGGER, long_date, pct, rs
from loader import SHORT_DATED_DAYS, View
from src import metrics
from src.metrics import SIGNAL_CASH_AND_CARRY, SIGNAL_NONE, SIGNAL_REVERSE

SIDES = {SIGNAL_CASH_AND_CARRY: "Cash-and-carry", SIGNAL_REVERSE: "Reverse arb"}
RANKINGS = {
    metrics.RANK_BY_ANNUALISED: f"Best annualised return ({SHORT_DATED_DAYS}+ days to expiry)",
    metrics.RANK_BY_RUPEES: "Most Rs per quintal (any days to expiry)",
    metrics.RANK_SHORT_DATED: f"Short-dated (under {SHORT_DATED_DAYS} days): exaggerated",
}
TOP_ANNOTATED = 5
DATE_KEY, LAST_PICK_KEY, TIMELINE_KEY = "calculator_date", "calculator_last_pick", "signal_timeline"


# --------------------------------------------------------------------------- #
# Calculator
# --------------------------------------------------------------------------- #


def money(amount: float) -> str:
    """Signed rupees; very small fees keep a third decimal so they do not show as zero."""
    if amount == 0:
        return "0.00"
    return f"{amount:+,.3f}" if abs(amount) < 0.05 else f"{amount:+,.2f}"


def calculator_table(title: str, lines: list[tuple], result: str) -> str:
    """One trade as an HTML table: what, how it is worked out, and the amount in Rs per quintal."""
    rows = "".join(
        f'<tr class="{"total" if label.startswith("=") else ""}"><td>{label.lstrip("= ")}</td>'
        f'<td class="formula">{formula}</td><td class="amount">{money(amount)}</td></tr>'
        for label, formula, amount in lines
    )
    return f'<div class="calc"><h5>{title}</h5><table>{rows}</table><div class="result">{result}</div></div>'


def render_calculator(view: View, day: pd.Series) -> None:
    calc = metrics.trade_calculator(day["spot"], day["settlement_price"], day["days_to_expiry"], day["tbill_pct"], view.costs)
    summary, days = calc["summary"], int(day["days_to_expiry"])
    caution = f" <span class='greyed'>({components.SHORT_DATED_NOTE})</span>" if days < SHORT_DATED_DAYS else ""

    st.markdown(
        f'<p class="legend-note">{long_date(day["date"])} · contract <b>{day["contract_symbol"]}</b> · {days} days to expiry · '
        f'spot <b>{rs(day["spot"])}</b> · futures <b>{rs(day["settlement_price"])}</b> · fair value {rs(summary["fair_value"])} · '
        f'91-day T-bill {pct(day["tbill_pct"])}. All amounts are Rs per quintal {DAGGER}.</p>',
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    with left:
        st.markdown(
            calculator_table(
                "▲ Cash-and-carry: buy spot, store, sell futures",
                calc["carry"],
                f"Holding-period return = carry profit / spot = <b>{pct(summary['carry_holding_pct'], signed=True)}</b> over {days} days.<br>"
                f"Annualised = that x 365 / {days} = <b>{pct(summary['carry_annualised_pct'], signed=True)}</b>{caution}, "
                f"which is <b>{summary['carry_excess_over_tbill']:+.2f} points</b> against the T-bill. "
                "Funding is kept out of the return so the T-bill comparison does not count interest twice.",
            ),
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            calculator_table(
                "▼ Reverse arb: stock holder sells spot, buys futures",
                calc["reverse"],
                f"Holding-period return = net benefit / spot = <b>{pct(summary['reverse_holding_pct'], signed=True)}</b> over {days} days.<br>"
                f"Annualised = that x 365 / {days} = <b>{pct(summary['reverse_annualised_pct'], signed=True)}</b>{caution}. Needs stock already held.<br>"
                f"Excluding avoided funding: <b>{pct(summary['reverse_ex_funding_annualised_pct'], signed=True)}</b> a year, "
                f"<b>{summary['reverse_excess_over_tbill']:+.2f} points</b> against the T-bill. The interest saved is itself earned at about the "
                "T-bill rate, so it is left out of this comparison to avoid counting it twice.",
            ),
            unsafe_allow_html=True,
        )

    verdict = {
        SIGNAL_CASH_AND_CARRY: f"cash-and-carry is tradeable: {rs(summary['carry_net_profit'], signed=True)} per quintal after every cost including funding",
        SIGNAL_REVERSE: f"reverse arb is tradeable: {rs(summary['reverse_benefit'], signed=True)} per quintal net benefit to a stock holder",
        SIGNAL_NONE: "neither side is tradeable: no trade makes money after all costs",
    }[day["tradeable_signal"]]
    st.markdown(
        f'<p class="takeaway">{components.signal_badge(day["tradeable_signal"])} &nbsp;On {long_date(day["date"])} {verdict}. '
        f"Futures were {pct(abs(summary['mispricing_pct']))} {'above' if summary['mispricing_pct'] >= 0 else 'below'} fair value.</p>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #


def timeline_figure(economics: pd.DataFrame, days: pd.DataFrame, chosen: pd.Timestamp) -> go.Figure:
    """Every trading day as a dot on the basis line: colour = tradeable side, size = annualised return."""
    figure = go.Figure()
    figure.add_scatter(
        x=economics["date"], y=economics["basis_pct"], mode="lines", name="Basis % (active contract)",
        line=dict(color=theme.FAINT, width=1), hoverinfo="skip",
    )  # fmt: skip
    idle = economics.loc[economics["tradeable_signal"] == SIGNAL_NONE]
    figure.add_scatter(
        x=idle["date"], y=idle["basis_pct"], mode="markers", name="■ No trade", customdata=idle["date"].dt.strftime("%Y-%m-%d"),
        marker=dict(symbol="square", size=5, color=theme.TBILL, opacity=0.7),
        hovertemplate="%{x|%d %b %Y}<br>basis %{y:.2f}%<br>no tradeable side<extra></extra>",
    )  # fmt: skip
    for side, (label, colour, shape, symbol) in {
        SIGNAL_CASH_AND_CARRY: ("Cash-and-carry", theme.CARRY, "▲", "triangle-up"),
        SIGNAL_REVERSE: ("Reverse arb", theme.REVERSE, "▼", "triangle-down"),
    }.items():
        own = days.loc[days["side"] == side]
        size = own["annualised_pct"].clip(lower=0, upper=90) / 90 * 15 + 6  # 6 to 21 px; capped so short-dated spikes do not dominate
        figure.add_scatter(
            x=own["date"], y=own["basis_pct"], mode="markers", name=f"{shape} {label}",
            marker=dict(symbol=symbol, size=size, color=colour, opacity=0.85, line=dict(color=theme.SURFACE, width=1)),
            customdata=pd.DataFrame({"date": own["date"].dt.strftime("%Y-%m-%d"), "net": own["net_rs"], "annual": own["annualised_pct"], "days": own["days_to_expiry"]}),
            hovertemplate="%{x|%d %b %Y}<br>basis %{y:.2f}%<br>net Rs %{customdata[1]:,.0f} per quintal<br>"
            "%{customdata[2]:.1f}% annualised, %{customdata[3]} days to expiry<extra>" + label + "</extra>",
        )  # fmt: skip
        best = metrics.best_dates(days, side, metrics.RANK_BY_ANNUALISED, top=TOP_ANNOTATED)
        for position, row in enumerate(best.itertuples()):
            above = side == SIGNAL_REVERSE  # reverse-arb days have a positive basis, so their labels go above
            figure.add_annotation(
                x=row.date, y=row.basis_pct, text=f"{row.date:%d %b}<br>{row.annualised_pct:.0f}%", showarrow=True, arrowhead=0, arrowwidth=1,
                arrowcolor=theme.MUTED, ax=0, ay=(-34 - 14 * (position % 2)) if above else (34 + 14 * (position % 2)),
                font=dict(size=10, color=theme.INK), bgcolor=theme.hex_to_rgba(theme.CARD, 0.85), bordercolor=colour, borderwidth=1, borderpad=2,
            )  # fmt: skip
    figure.add_vline(x=chosen, line_width=1.5, line_dash="dash", line_color=theme.ACCENT)
    figure.add_hline(y=0, line_width=1, line_color=theme.FAINT)
    figure.update_layout(yaxis=dict(title="Basis (spot - futures), % of spot", ticksuffix="%"), hovermode="closest", legend=dict(font=dict(size=11)))
    return figure


def picked_date(event) -> str | None:
    """The date of the point clicked on the timeline, if any."""
    points = (event or {}).get("selection", {}).get("points", [])
    if not points:
        return None
    custom = points[0].get("customdata")
    return (custom[0] if isinstance(custom, (list, tuple)) else custom) if custom is not None else None


# --------------------------------------------------------------------------- #
# Best-dates tables
# --------------------------------------------------------------------------- #

TABLE_COLUMNS = {
    "date": st.column_config.DateColumn("Date", format="DD MMM YYYY", pinned=True, width=95),
    "contract_symbol": st.column_config.TextColumn("Contract", width=150),
    "days_to_expiry": st.column_config.NumberColumn("Days to expiry", width=85),
    "spot": st.column_config.NumberColumn("Spot", format="%.2f", width=80),
    "futures": st.column_config.NumberColumn("Futures", format="%.2f", width=80),
    "basis_pct": st.column_config.NumberColumn("Basis %", format="%.2f", width=70),
    "net_rs": st.column_config.NumberColumn("Net Rs/qtl", format="%.2f", width=85),
    "holding_pct": st.column_config.NumberColumn("Holding %", format="%.2f", width=80),
    "annualised_pct": st.column_config.NumberColumn("Annualised %", format="%.2f", width=95),
    "excess_over_tbill": st.column_config.NumberColumn("Over T-bill, pts", format="%.2f", width=105),
    "final_settlement": st.column_config.NumberColumn("Settled at", format="%.2f", width=85),
    "convergence_error_pct": st.column_config.NumberColumn("Settled vs 3-day spot %", format="%.3f", width=150),
}


# Returns come before prices, so the T-bill comparison is visible on a laptop screen without scrolling.
COLUMN_ORDER = [
    "date", "contract_symbol", "days_to_expiry", "net_rs", "holding_pct", "annualised_pct", "ex_funding_annualised_pct",
    "excess_over_tbill", "spot", "futures", "basis_pct", "final_settlement", "convergence_error_pct",
]  # fmt: skip


def table_columns(side: str) -> dict:
    """Columns for one side's table.

    Reverse arb gets an extra column: its return excluding avoided funding, which
    is what the T-bill column is measured from. For cash-and-carry the annualised
    return is already before funding, so no extra column is needed.
    """
    columns = dict(TABLE_COLUMNS)
    if side == SIGNAL_REVERSE:
        columns["annualised_pct"] = st.column_config.NumberColumn("Annualised % (full)", format="%.2f", width=105)
        columns["ex_funding_annualised_pct"] = st.column_config.NumberColumn("Annualised % excl. avoided funding", format="%.2f", width=120)
        columns["excess_over_tbill"] = st.column_config.NumberColumn("Excl. funding vs T-bill, pts", format="%.2f", width=120)
    return {key: columns[key] for key in COLUMN_ORDER if key in columns}


def all_rankings(days: pd.DataFrame, side: str) -> pd.DataFrame:
    """The three top-10 lists for one side in one table, for the CSV download."""
    lists = [metrics.best_dates(days, side, ranking).assign(ranking=label) for ranking, label in RANKINGS.items()]
    return pd.concat(lists, ignore_index=True)[["ranking", *table_columns(side)]]


def render_best_dates(view: View, days: pd.DataFrame, economics: pd.DataFrame) -> None:
    for side, label in SIDES.items():
        _, colour, shape = components.signal_style(side)
        st.markdown(f'<p class="question"><span style="color:{colour}">{shape}</span> Best dates for {label.lower()} {DAGGER}</p>', unsafe_allow_html=True)
        ranking = st.radio("Rank by", list(RANKINGS), format_func=RANKINGS.get, horizontal=True, key=f"ranking_{side}", label_visibility="collapsed")
        table = metrics.best_dates(days, side, ranking)
        if table.empty:
            st.info(f"No tradeable {label.lower()} day in this list for the selected date range.")
        else:
            columns = table_columns(side)
            st.dataframe(table[list(columns)], column_config=columns, hide_index=True, height=(len(table) + 1) * 35 + 3)
        if side == SIGNAL_REVERSE:
            st.caption(
                "Reverse-arb return includes the interest saved on the cash (avoided funding). That interest is earned at about the T-bill rate, "
                "so comparing the full return with the T-bill would count it twice. The T-bill column therefore uses the return excluding avoided funding."
            )
        if ranking == metrics.RANK_SHORT_DATED:
            st.caption(f"Under {SHORT_DATED_DAYS} days to expiry: the annualised column multiplies a small gain by a large factor. Read Net Rs/qtl and Holding % instead.")
        narrative = metrics.describe_best_dates(view.name, side, days, economics, view.details.get("arrival_months"))
        st.markdown(f'<p class="takeaway">{narrative}</p>', unsafe_allow_html=True)

    left, right, _ = st.columns([1.4, 1.4, 1.2])
    for column, (side, label) in zip((left, right), SIDES.items()):
        column.download_button(
            f"Download {label.lower()} dates (CSV)", all_rankings(days, side).to_csv(index=False),
            file_name=f"{view.commodity.lower()}_{side.lower()}_best_dates.csv", mime="text/csv", key=f"download_{side}",
        )  # fmt: skip
    st.caption(
        "Net Rs/qtl is profit after every cost including funding. Holding % and Annualised % are before funding for cash-and-carry, so they can be "
        "compared with the T-bill. 'Settled at' and 'Settled vs 3-day spot' show how the contract actually ended, once it has expired. "
        "Arrival-season months in the text above are the typical arrival season (general market knowledge, not from exchange data)."
    )


# --------------------------------------------------------------------------- #
# Tab
# --------------------------------------------------------------------------- #


def render(view: View) -> None:
    economics = view.tables["economics"]
    if not components.has_rows(economics, "The selected date range contains no trading days."):
        return
    days = metrics.signal_days(economics, view.full["expiry_settlements"])
    dates = list(economics["date"])

    # A click on the timeline (drawn further down) arrives in session state before this run starts.
    pick = picked_date(st.session_state.get(TIMELINE_KEY))
    if pick and pick != st.session_state.get(LAST_PICK_KEY):
        st.session_state[LAST_PICK_KEY] = pick
        st.session_state[DATE_KEY] = pd.Timestamp(pick)
    if st.session_state.get(DATE_KEY) not in dates:
        st.session_state[DATE_KEY] = dates[-1]

    st.markdown('<p class="question">What does each trade earn on a given day?</p>', unsafe_allow_html=True)
    chosen = st.selectbox("Trade date (or click a point on the timeline below)", dates, format_func=long_date, key=DATE_KEY)
    render_calculator(view, economics.loc[economics["date"] == chosen].iloc[0])

    carry_days, reverse_days = (int((days["side"] == side).sum()) for side in SIDES)
    components.chart(
        timeline_figure(economics, days, chosen),
        f"When was each side tradeable, and how good was it? {DAGGER}",
        f"Each marker is a trading day on the basis line (spot minus futures; below zero means futures above spot): {carry_days} cash-and-carry days (▲), {reverse_days} reverse-arb days (▼), "
        f"{len(economics) - carry_days - reverse_days} with no trade (■). Bigger markers earned more a year (capped at 90%). "
        f"Labels mark the top {TOP_ANNOTATED} dates of each side with {SHORT_DATED_DAYS}+ days to expiry. Click a marker to load that day into the calculator.",
        key=TIMELINE_KEY, height=420, on_select="rerun", selection_mode="points",
    )  # fmt: skip
    render_best_dates(view, days, economics)
