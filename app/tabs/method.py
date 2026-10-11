"""Tab 6: the trade in plain words, where the data comes from, and what is assumed."""

import pandas as pd
import streamlit as st

import loader
from formatting import long_date, pct
from loader import View

TRADE_STEPS = [
    ("1. Buy spot", "at the mandi, at today's spot price"),
    ("2. Assay and store", "in an NCCL-accredited warehouse"),
    ("3. Sell futures", "the same day, locking in the futures premium"),
    ("4. Deliver at expiry", "and receive the futures price"),
]
TRADE_DIAGRAM = '<div class="flow">' + '<div class="flow-arrow">→</div>'.join(
    f'<div class="flow-step"><b>{title}</b>{detail}</div>' for title, detail in TRADE_STEPS
) + "</div>"

SOURCES = [
    ("Futures settlement price, open interest, volume, expiry dates", "NCDEX daily bhavcopy (UDiFF)", "https://www.ncdex.com/markets/bhavcopy"),
    ("Spot price at the basis centre", "Same bhavcopy, column UndrlygPric (last polled spot of the day)", "https://www.ncdex.com/markets/spotprices"),
    ("91-day T-bill yield, daily", "FBIL T-bill curve, 3-month point", "https://www.fbil.org.in/"),
    ("91-day T-bill yield, latest days", "RBI auction cut-off yield (fills days FBIL has not published)", "https://www.rbi.org.in/"),
    ("Storage and assaying charges", "NCCL Storage and Assaying Charges, 8 Oct 2026", "https://www.nccl.co.in/warehousing/warehouse-data"),
    ("Exchange transaction fee", "NCDEX Master Circular, Trading Operations (2026)", "https://www.ncdex.com/circulars"),
]

NOT_MODELLED = [
    "Repository (NERL) and repository-participant charges, which are added to the NCCL rates",
    "Minimum holding periods for storage charges",
    "Margin funding on the futures leg",
    "Mandi fees, commission and transport on the spot purchase",
    "Quality premiums and discounts at delivery, and the risk of a lot failing assay",
    "GST on the goods themselves (a cash-flow item for a registered trader, not a cost)",
]


def cost_model_table(view: View) -> pd.DataFrame:
    """Each cost input with its value for this commodity and whether it is sourced or assumed."""
    _, published, _ = loader.load_config()
    costs = view.costs
    storage, assaying = published["warehouse_storage"][view.commodity], published["assaying"][view.commodity]
    gst_switch = "applied" if costs.gst_on_storage_and_assaying else "not applied"
    rows = [
        ("Storage (includes insurance and fumigation)", f"Rs {costs.storage_rs_per_quintal_per_day:.2f} per quintal per day", "Confirmed", storage["source"]),
        ("Assaying", f"Rs {costs.assaying_rs_per_quintal:.0f} per quintal (Rs {assaying['value']:,.0f} per {assaying['sample_lot_mt']} MT lot)", "Confirmed", assaying["source"]),
        ("Exchange transaction fee", f"Rs {costs.exchange_fee_rs_per_lakh} per lakh of turnover", "Confirmed", "NCDEX master circular"),
        ("SEBI turnover fee", f"Rs {costs.sebi_fee_rs_per_crore:.0f} per crore (agri rate)", "Confirmed", "SEBI notification, 2019"),
        ("Stamp duty", f"{costs.stamp_duty_pct}% on futures purchases only", "Confirmed", "NSE levies page"),
        ("Commodities transaction tax", "Nil: exempt agricultural commodity", "Confirmed", "NCDEX compliance guide"),
        ("GST on brokerage and exchange fee", f"{costs.gst_pct:.0f}%", "Confirmed", "NSE levies page"),
        ("Funding", f"91-day T-bill + {costs.funding_spread_pct:.2f}% a year", "Desk input", "Your cost of money"),
        ("Brokerage", f"Rs {costs.brokerage_rs_per_lakh:.0f} per lakh of turnover", "Desk input", "Negotiated"),
        ("Handling (loading, unloading) †", f"Rs {costs.handling_rs_per_quintal:.0f} per quintal", "Assumption", "Not published; paid directly to the warehouse"),
        ("GST on storage and assaying †", f"{costs.gst_pct:.0f}%, currently {gst_switch}", "Assumption", "Treatment for these commodities not verified"),
    ]
    return pd.DataFrame(rows, columns=["Cost", "Value used", "Status", "Source"])


def render(view: View) -> None:
    futures, _, tbill = loader.load_tables()
    settlements = view.tables["expiry_settlements"]
    days = futures["date"].nunique()

    st.markdown("#### The trade in four lines")
    st.markdown(TRADE_DIAGRAM, unsafe_allow_html=True)
    st.markdown(
        "- **Cash-and-carry.** Buy the commodity today, store it in an exchange-accredited warehouse, sell futures against it, deliver at expiry.\n"
        "- **What it earns.** The futures premium (futures minus spot), locked in on day one, less funding, storage, assaying, handling and fees.\n"
        "- **When it pays.** When that return beats the cost of money. The benchmark here is the 91-day T-bill.\n"
        "- **Reverse arb.** When futures are below fair value, a stock holder sells spot and buys futures. It needs existing stock, so the gap can persist."
    )

    st.markdown("#### Why the data can be trusted")
    st.markdown(
        f"- **Spot matches the exchange.** The spot series matched the NCDEX spot price page exactly on 9 Oct 2026: "
        f"guar seed (Jodhpur) Rs 7,706.70 and jeera (Unjha) Rs 24,626.00. One date, checked by hand.\n"
        f"- **Contracts settle where the model says.** For {view.name.lower()}, {len(settlements)} expired contracts settled within "
        f"{pct(settlements['convergence_error_pct'].abs().mean(), decimals=3)} of the 3-day average spot on average "
        "(0.07% across both commodities). The 3-day rule is inferred from the data, not read from the contract specification.\n"
        f"- **Complete history.** {days} trading days from {long_date(futures['date'].min())} to {long_date(futures['date'].max())}, "
        "with no failed downloads; the 15 missing weekdays are exchange holidays.\n"
        "- **Spot is consistent.** Every contract of a commodity carries the same spot price on every day."
    )

    st.markdown("#### Data sources")
    sources = pd.DataFrame(SOURCES, columns=["Data", "Source", "Link"])
    st.dataframe(sources, hide_index=True, column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")})
    rbi_days = int((tbill["source"] == "rbi_fill").sum())
    st.caption(f"T-bill: {int((tbill['source'] == 'fbil').sum())} days from FBIL, {int((tbill['source'] == 'ffill').sum())} carried forward, {rbi_days} filled with the RBI auction yield.")

    st.markdown(f"#### Cost model for {view.name.lower()}")
    cost_table = cost_model_table(view)
    st.dataframe(cost_table, hide_index=True, height=(len(cost_table) + 1) * 35 + 3)
    st.caption("† Assumptions. Change them under Desk inputs and every figure on the dashboard is recomputed.")

    st.markdown("#### Not modelled")
    st.markdown("\n".join(f"- {item}" for item in NOT_MODELLED))

    st.markdown("#### Definitions")
    st.markdown(
        "- **Basis:** spot minus futures. Negative when futures are above spot (the cash-and-carry side), positive when futures are below spot (the reverse-arb side). "
        "The futures premium, futures minus spot, is the same number with its sign flipped.\n"
        "- **Active contract:** the near month, until it has fewer than the roll threshold of days left; then the next month.\n"
        "- **Net carry return:** annualised return on the cash invested, after physical and transaction costs but before funding. Compare it with the T-bill.\n"
        "- **Net profit:** the same trade after funding at T-bill plus the spread.\n"
        "- **Fair value:** spot plus funding and storage to expiry. A raw signal is a gap to fair value larger than the threshold.\n"
        "- **Tradeable signal:** a raw signal that also makes money after all costs, including one-off assaying and handling.\n"
        "- **Break-even storage:** the storage rate at which cash-and-carry net profit is exactly zero."
    )
