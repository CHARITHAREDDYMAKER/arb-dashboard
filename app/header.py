"""Header shown above every tab: the KPI row and the three generated insights.

It always describes the latest trading day and the full history, whatever date
range is selected in the sidebar.
"""

import streamlit as st

import components
from formatting import DAGGER, long_date, pct, rs
from loader import SHORT_DATED_DAYS, View
from src.metrics import SIGNAL_REVERSE


def trade_card(view: View) -> dict:
    """The headline return card for whichever side is tradeable (cash-and-carry if neither).

    A short-dated contract leads with rupees and the holding-period return;
    the annualised figure moves underneath with a caution.
    """
    snap = view.snapshot
    if snap["tradeable_signal"] == SIGNAL_REVERSE:
        label, profit, holding, annual = "Reverse arb", snap["rev_benefit"], snap["rev_benefit_pct"], snap["rev_return_pct"]
    else:
        label, profit, holding, annual = "Cash-and-carry", snap["cc_net_profit"], snap["cc_net_profit_pct"], snap["cc_carry_return_pct"]
    tbill = f"T-bill {pct(snap['tbill_pct'])}" + (" (RBI fill)" if snap["tbill_is_rbi_fill"] else "")
    days = snap["days_to_expiry"]

    if days < SHORT_DATED_DAYS:
        return dict(
            label=f"{label} net {DAGGER}",
            value=f"{rs(profit, signed=True)}",
            sub=f"per quintal · {pct(holding, signed=True)} over {days} days<br>"
            f"<span class='greyed'>{pct(annual, signed=True)} annualised · {components.SHORT_DATED_NOTE}</span>",
        )
    return dict(
        label=f"{label} return {DAGGER}",
        value=f"{pct(annual, signed=True)} <span class='unit'>a year</span>",
        sub=f"{tbill} · net {rs(profit, signed=True)} per quintal over {days} days",
    )


def render(view: View) -> None:
    snap = view.snapshot
    side = "above" if snap["mispricing_pct"] >= 0 else "below"
    if snap["break_even_storage"] > 0:
        break_even, break_even_note = rs(snap["break_even_storage"]), f"storage, per quintal per day · actual {rs(snap['actual_storage'])}"
    else:
        break_even, break_even_note = "None", f"futures do not cover the other costs · actual storage {rs(snap['actual_storage'])}"

    st.markdown(f"## {view.details['name']} · cash-futures arbitrage")
    st.markdown(
        f'<p class="legend-note">Latest trading day: <b>{long_date(snap["as_of"])}</b>. The header and insights always use this day '
        "and the full history; the date range filters the charts in the tabs.</p>",
        unsafe_allow_html=True,
    )
    components.kpi_row(
        [
            dict(label="Spot", value=rs(snap["spot"]), sub=f"{view.details['basis_centre']} · {long_date(snap['as_of'])}"),
            dict(label="Active futures", value=rs(snap["active_futures"]), sub=f"expires {long_date(snap['expiry'])} · {snap['days_to_expiry']} days"),
            dict(label="Basis", value=rs(snap["basis"], signed=True), sub=f"{pct(snap['basis_pct'], signed=True)} of spot"),
            dict(
                label=f"Signal {DAGGER}",
                value=components.signal_badge(snap["tradeable_signal"]),
                sub=f"tradeable after all costs · futures {pct(abs(snap['mispricing_pct']))} {side} fair value",
            ),
            trade_card(view),
            dict(label=f"Break-even rent {DAGGER}", value=break_even, sub=break_even_note),
        ],
        widths=[1, 1, 1, 1.15, 1.6, 1.1],  # the trade card carries the most text
    )
    st.markdown("".join(f'<p class="insight">• {snap[f"insight_{number}"]}</p>' for number in (1, 2, 3)), unsafe_allow_html=True)
    components.dagger_legend()
