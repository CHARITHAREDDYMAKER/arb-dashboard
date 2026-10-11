"""Banner, KPI row and generated insights, shown above every tab.

Everything here describes the latest trading day and the full history,
whatever date range is selected in the sidebar.
"""

import streamlit as st

import components
import theme
from formatting import DAGGER, long_date, pct, rs
from loader import SHORT_DATED_DAYS, View
from src.metrics import SIGNAL_REVERSE

SPARK_DAYS = 30


def ticker_item(peer: dict) -> str:
    label, _, shape = components.signal_style(peer["signal"])
    return (
        f"<span><b>{peer['name'].upper()}</b> &nbsp;spot <b>{rs(peer['spot'])}</b> {components.delta(peer['spot_change_pct'], pct(peer['spot_change_pct'], signed=True))}"
        f" &nbsp;·&nbsp; futures <b>{rs(peer['active_futures'])}</b> &nbsp;·&nbsp; basis <b>{pct(peer['basis_pct'], signed=True)}</b>"
        f" &nbsp;·&nbsp; {shape} {label}</span>"
    )


def banner(view: View) -> None:
    """Project name, a slowly scrolling ticker for both commodities, and the data date."""
    items = "".join(ticker_item(peer) for peer in view.peers.to_dict("records"))
    st.markdown(
        '<div class="banner">'
        '<div class="brand">CASH-FUTURES <span>ARBITRAGE MONITOR</span></div>'
        f'<div class="ticker" aria-label="latest prices"><div class="ticker-track">{items * 4}</div></div>'
        f'<div class="asof"><span class="live-dot"></span>NCDEX close · <b>{long_date(view.snapshot["as_of"])}</b></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def trade_card(view: View, recent) -> dict:
    """The headline return card for whichever side is tradeable (cash-and-carry if neither).

    A short-dated contract leads with rupees and the holding-period return;
    the annualised figure moves underneath with a caution.
    """
    snap = view.snapshot
    reverse = snap["tradeable_signal"] == SIGNAL_REVERSE
    if reverse:
        label, profit, holding, annual, column, colour = "Reverse arb", snap["rev_benefit"], snap["rev_benefit_pct"], snap["rev_return_pct"], "rev_benefit", theme.REVERSE
    else:
        label, profit, holding, annual, column, colour = "Cash-and-carry", snap["cc_net_profit"], snap["cc_net_profit_pct"], snap["cc_carry_return_pct"], "cc_net_profit", theme.CARRY
    funding_label = "after funding" if reverse else "before funding"
    tbill = f"T-bill {pct(snap['tbill_pct'])}" + (" (RBI fill)" if snap["tbill_is_rbi_fill"] else "")
    days = snap["days_to_expiry"]
    spark = components.sparkline(recent[column], colour)

    if days < SHORT_DATED_DAYS:
        return dict(
            label=f"{label} net {DAGGER}",
            value=f"{rs(profit, signed=True)}",
            sub=f"per quintal · {pct(holding, signed=True)} over {days} days<br>"
            f"<span class='greyed'>{pct(annual, signed=True)} annualised {funding_label} · {components.SHORT_DATED_NOTE}</span>",
            spark=spark,
        )
    # Beside the T-bill, reverse arb shows its return excluding avoided funding (see metrics.reverse_arb).
    against_tbill = f"{pct(snap['rev_return_ex_funding_pct'], signed=True)} excl. avoided funding vs {tbill}" if reverse else tbill
    return dict(
        label=f"{label} return {DAGGER}",
        value=f"{pct(annual, signed=True)} <span class='unit'>a year, {funding_label}</span>",
        sub=f"{against_tbill} · net {rs(profit, signed=True)} per quintal over {days} days",
        spark=spark,
    )


def render(view: View) -> None:
    snap = view.snapshot
    economics = view.full["economics"]
    recent = economics.tail(SPARK_DAYS)
    today, yesterday = economics.iloc[-1], economics.iloc[-2]
    same_contract = today["contract_symbol"] == yesterday["contract_symbol"]

    spot_change = (today["spot"] / yesterday["spot"] - 1) * 100
    futures_change = (today["settlement_price"] / yesterday["settlement_price"] - 1) * 100 if same_contract else None
    basis_change = today["basis"] - yesterday["basis"] if same_contract else None
    side = "above" if snap["mispricing_pct"] >= 0 else "below"
    if snap["break_even_storage"] > 0:
        break_even, break_even_note = rs(snap["break_even_storage"]), f"storage, per quintal per day · actual {rs(snap['actual_storage'])}"
    else:
        break_even, break_even_note = "None", f"futures do not cover the other costs · actual storage {rs(snap['actual_storage'])}"

    banner(view)
    st.markdown(
        f'<p class="legend-note"><b style="color:{theme.INK}; font-size:1.05rem">{view.details["name"]}</b> &nbsp;·&nbsp; '
        f'latest trading day <b>{long_date(snap["as_of"])}</b>. The header always shows this day and the full history; '
        "the date range filters the charts in the tabs. Arrows compare with the previous trading day; lines show the last 30.</p>",
        unsafe_allow_html=True,
    )
    components.kpi_row(
        [
            dict(
                label="Spot",
                value=rs(snap["spot"]),
                sub=f"{components.delta(spot_change, pct(spot_change, signed=True))} · {view.details['basis_centre']}",
                spark=components.sparkline(recent["spot"], theme.SPOT),
            ),
            dict(
                label="Active futures",
                value=rs(snap["active_futures"]),
                sub=(f"{components.delta(futures_change, pct(futures_change, signed=True))} · " if same_contract else "rolled today · ")
                + f"expires {long_date(snap['expiry'])}, {snap['days_to_expiry']} days",
                spark=components.sparkline(recent["settlement_price"], theme.FUTURES),
            ),
            dict(
                label="Basis (spot - futures)",
                value=rs(snap["basis"], signed=True),
                sub=f"{pct(snap['basis_pct'], signed=True)} of spot · futures {'above' if snap['basis'] < 0 else 'below'} spot"
                + (f" · {components.delta(basis_change, rs(basis_change, 0, signed=True))}" if same_contract else ""),
                spark=components.sparkline(recent["basis_pct"], theme.FUTURES),
            ),
            dict(
                label=f"Signal {DAGGER}",
                value=components.signal_badge(snap["tradeable_signal"]),
                sub=f"tradeable after all costs · futures {pct(abs(snap['mispricing_pct']))} {side} fair value",
                spark=components.sparkline(recent["mispricing_pct"], theme.MUTED),
            ),
            trade_card(view, recent),
            dict(
                label=f"Break-even rent {DAGGER}",
                value=break_even,
                sub=break_even_note,
                spark=components.sparkline(recent["break_even_storage"].clip(-15, 25), theme.CARRY),
            ),
        ],
        widths=[1, 1.05, 1, 1.15, 1.5, 1.1],  # the trade card carries the most text
    )
    st.markdown("".join(f'<p class="insight">• {snap[f"insight_{number}"]}</p>' for number in (1, 2, 3)), unsafe_allow_html=True)
    components.dagger_legend()
