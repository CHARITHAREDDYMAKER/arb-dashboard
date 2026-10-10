"""Header shown above every tab: the KPI row and the three generated insights."""

import streamlit as st

import components
from formatting import DAGGER, long_date, pct, rs
from loader import SHORT_DATED_DAYS, View


def render(view: View) -> None:
    snap = view.snapshot
    short_dated = snap["days_to_expiry"] < SHORT_DATED_DAYS
    side = "above" if snap["mispricing_pct"] >= 0 else "below"
    tbill_note = " (RBI fill)" if snap["tbill_is_rbi_fill"] else ""
    footnote = f"<br><i>{components.SHORT_DATED_NOTE}</i>" if short_dated else ""
    if snap["break_even_storage"] > 0:
        break_even, break_even_note = rs(snap["break_even_storage"]), f"storage, per quintal per day · actual {rs(snap['actual_storage'])}"
    else:
        break_even, break_even_note = "None", f"futures do not cover the other costs · actual storage {rs(snap['actual_storage'])}"

    st.markdown(f"## {view.details['name']} · cash-futures arbitrage")
    components.kpi_row(
        [
            dict(label="Spot", value=rs(snap["spot"]), sub=f"{view.details['basis_centre']} · {long_date(snap['as_of'])}"),
            dict(
                label="Active futures",
                value=rs(snap["active_futures"]),
                sub=f"expires {long_date(view.tables['economics'].iloc[-1]['expiry'])} · {snap['days_to_expiry']} days",
            ),
            dict(label="Basis", value=rs(snap["basis"], signed=True), sub=f"{pct(snap['basis_pct'], signed=True)} of spot"),
            dict(
                label=f"Signal {DAGGER}",
                value=components.signal_badge(snap["tradeable_signal"]),
                sub=f"tradeable after all costs · futures {pct(abs(snap['mispricing_pct']))} {side} fair value",
            ),
            dict(
                label=f"Carry return {DAGGER}",
                value=f"{pct(snap['cc_carry_return_pct'], signed=True)} <span style='font-size:0.8rem'>p.a.</span>",
                sub=f"T-bill {pct(snap['tbill_pct'])}{tbill_note}{footnote}",
                greyed=short_dated,
            ),
            dict(
                label=f"Break-even rent {DAGGER}",
                value=break_even,
                sub=break_even_note,
            ),
        ]
    )
    st.markdown("".join(f'<p class="insight">• {snap[f"insight_{number}"]}</p>' for number in (1, 2, 3)), unsafe_allow_html=True)
    components.dagger_legend()
