"""Phase 3 report: key numbers per commodity, written to reports/phase3_metrics.md."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from src import metrics, settings
from src.metrics import SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "phase3_metrics.md"
ASSUMED = "†"  # marks numbers that depend on the handling placeholder or the GST-on-storage switch


def to_markdown(frame: pd.DataFrame) -> str:
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    divider = "|" + "---|" * len(frame.columns)
    rows = ["| " + " | ".join(map(str, row)) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *rows])


def key_numbers(commodity: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """The headline figures for one commodity as a two-column table."""

    def own(name: str) -> pd.DataFrame:
        return tables[name].loc[tables[name]["commodity"] == commodity]

    economics, snap = own("economics"), own("snapshot").iloc[0]
    summary = own("opportunity_summary").set_index("signal")
    shares = metrics.signal_shares(economics)
    raw_shares = metrics.signal_shares(economics, "signal")
    settlements, roll, liquidity = own("expiry_settlements"), own("rollover"), own("liquidity")
    curve = own("term_structure_daily")
    profitable = economics.loc[economics["cc_net_profit"] > 0]

    def side(signal: str, column: str) -> str:
        return f"{summary.loc[signal, column]:.1f}%" if signal in summary.index else "n/a"

    rows = [
        ("**Current snapshot**", f"as of {snap['as_of'].date()}"),
        ("Spot (Rs/quintal)", f"{snap['spot']:,.2f}"),
        ("Active contract", f"{snap['active_contract']}, {snap['days_to_expiry']} days to expiry"),
        ("Active futures", f"{snap['active_futures']:,.2f}"),
        ("Basis (spot - futures)", f"{snap['basis']:+,.2f} ({snap['basis_pct']:+.2f}%)"),
        (f"Fair value {ASSUMED}", f"{snap['fair_value']:,.2f}; futures {snap['mispricing_pct']:+.2f}% vs fair"),
        (f"Raw signal (vs fair value) {ASSUMED}", snap["signal"]),
        (f"Tradeable signal (profitable after all costs) {ASSUMED}", snap["tradeable_signal"]),
        (f"Cash-and-carry net profit {ASSUMED}", f"Rs {snap['cc_net_profit']:+,.2f} per quintal"),
        (f"Cash-and-carry return, annualised {ASSUMED}", f"{snap['cc_carry_return_pct']:+.1f}%"),
        ("91-day T-bill", f"{snap['tbill_pct']:.2f}%" + (" (RBI fill)" if snap["tbill_is_rbi_fill"] else "")),
        (f"Excess over T-bill {ASSUMED}", f"{snap['cc_excess_over_tbill']:+.1f} points"),
        (f"Reverse-arb return, annualised {ASSUMED}", f"{snap['rev_return_pct']:+.1f}%"),
        (f"Break-even storage now {ASSUMED}", f"Rs {snap['break_even_storage']:.2f} per quintal per day (actual {snap['actual_storage']:.2f})"),
        ("**Over the year**", f"{len(economics)} trading days"),
        (
            f"Days per raw signal {ASSUMED}",
            f"cash-and-carry {raw_shares[SIGNAL_CASH_AND_CARRY]:.1f}%, reverse {raw_shares[SIGNAL_REVERSE]:.1f}%, none {raw_shares['NONE']:.1f}%",
        ),
        (
            f"Days per tradeable signal {ASSUMED}",
            f"cash-and-carry {shares[SIGNAL_CASH_AND_CARRY]:.1f}%, reverse {shares[SIGNAL_REVERSE]:.1f}%, none {shares['NONE']:.1f}%",
        ),
        (
            f"Locked return on tradeable cash-and-carry days, annualised {ASSUMED}",
            f"mean {side(SIGNAL_CASH_AND_CARRY, 'avg_locked_return_ann_pct')}, median {side(SIGNAL_CASH_AND_CARRY, 'median_locked_return_ann_pct')}, best {side(SIGNAL_CASH_AND_CARRY, 'best_locked_return_ann_pct')}",
        ),
        (
            f"Locked return on tradeable reverse days, annualised {ASSUMED}",
            f"mean {side(SIGNAL_REVERSE, 'avg_locked_return_ann_pct')}, median {side(SIGNAL_REVERSE, 'median_locked_return_ann_pct')}, best {side(SIGNAL_REVERSE, 'best_locked_return_ann_pct')}",
        ),
        (f"Days cash-and-carry made money after all costs {ASSUMED}", f"{len(profitable) / len(economics) * 100:.1f}%"),
        (
            f"On those days: median return / excess over T-bill {ASSUMED}",
            f"{profitable['cc_carry_return_pct'].median():.1f}% / {profitable['cc_excess_over_tbill'].median():+.1f} points",
        ),
        (
            f"Break-even storage over the year {ASSUMED}",
            f"median Rs {economics['break_even_storage'].median():.2f}; above the actual rate on "
            f"{(economics['break_even_storage'] > economics['actual_storage']).mean() * 100:.1f}% of days",
        ),
        (
            "Convergence error at expiry (vs 3-day average spot)",
            f"mean absolute {settlements['convergence_error_pct'].abs().mean():.3f}% over {len(settlements)} contracts",
        ),
        (
            "Typical roll cost (next minus near, last 5 trading days)",
            f"Rs {roll['calendar_spread'].mean():,.0f} per quintal, {roll['calendar_spread_ann_pct'].mean():.1f}% annualised",
        ),
        (
            "Curve shape",
            f"contango on {(curve['shape'] == 'contango').mean() * 100:.1f}% of days; average slope {curve['slope_pct_per_month'].mean():.2f}% per month",
        ),
        (
            "Capacity (10% of 20-day average volume)",
            f"Rs {liquidity['capacity_rs_crore'].iloc[-1]:.1f} crore a day now; median Rs {liquidity['capacity_rs_crore'].median():.1f} crore",
        ),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def sensitivity(tables: dict[str, pd.DataFrame], tbill: pd.DataFrame) -> pd.DataFrame:
    """How the two remaining assumptions move the result: share of days cash-and-carry made money."""
    rules = settings.load_rules()
    rows = []
    for commodity in settings.load_commodities():
        base = settings.load_cost_inputs(commodity)
        cases = {
            "Base case (GST on storage, handling Rs 10)": base,
            "No GST on storage and assaying": replace(base, gst_on_storage_and_assaying=False),
            "Handling Rs 0": replace(base, handling_rs_per_quintal=0.0),
            "Handling Rs 25": replace(base, handling_rs_per_quintal=25.0),
        }
        own_basis = tables["basis"].loc[tables["basis"]["commodity"] == commodity]
        for label, costs in cases.items():
            economics = metrics.daily_economics(own_basis, tbill, costs, rules)
            rows.append(
                {
                    "commodity": commodity,
                    "case": label,
                    "days_profitable_pct": round((economics["cc_net_profit"] > 0).mean() * 100, 1),
                    "median_net_profit_rs": round(economics["cc_net_profit"].median(), 1),
                    "reverse_signal_days_pct": round((economics["signal"] == SIGNAL_REVERSE).mean() * 100, 1),
                }
            )
    return pd.DataFrame(rows)


def findings(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Three findings for the interview, each built from the computed numbers."""
    economics, curve = tables["economics"], tables["term_structure_daily"]
    buckets = tables["convergence_buckets"].set_index(["commodity", "dte_bucket"])
    settlements, liquidity = tables["expiry_settlements"], tables["liquidity"]

    def share(commodity: str, signal: str) -> float:
        own = economics.loc[economics["commodity"] == commodity]
        return (own["signal"] == signal).mean() * 100

    def tradeable(commodity: str, signal: str) -> float:
        own = economics.loc[economics["commodity"] == commodity]
        return (own["tradeable_signal"] == signal).mean() * 100

    def profitable(commodity: str) -> float:
        own = economics.loc[economics["commodity"] == commodity]
        return (own["cc_net_profit"] > 0).mean() * 100

    slope = curve.groupby("commodity")["slope_pct_per_month"].mean()
    contango = curve.groupby("commodity")["shape"].apply(lambda shape: (shape == "contango").mean() * 100)
    capacity = liquidity.groupby("commodity")["capacity_rs_crore"].median()
    return [
        "### 1. The carry is in the calendar spread, not in spot versus the near month",
        "",
        f"The futures curve was in contango on {contango['JEERA']:.1f}% of days for jeera and {contango['GUARSEED']:.1f}% for guar seed, "
        f"sloping up about {slope['JEERA']:.1f}% and {slope['GUARSEED']:.1f}% a month (roughly {slope['JEERA'] * 12:.0f}% and {slope['GUARSEED'] * 12:.0f}% a year), "
        "well above the cost of money plus warehouse rent. Yet the contract nearest to expiry trades at or below spot. "
        "With basis defined as spot minus futures (negative = futures above spot), the "
        f"average basis 6 to 10 days before expiry is {buckets.loc[('JEERA', '6-10'), 'mean_basis_pct']:+.2f}% for jeera and "
        f"{buckets.loc[('GUARSEED', '6-10'), 'mean_basis_pct']:+.2f}% for guar seed, against "
        f"{buckets.loc[('JEERA', '46-60'), 'mean_basis_pct']:+.2f}% and {buckets.loc[('GUARSEED', '46-60'), 'mean_basis_pct']:+.2f}% at 46 to 60 days. "
        "A contract loses its premium as it approaches delivery. The practical reading: enter cash-and-carry early, 30 to 60 days out, "
        "or hold stock and sell the next month, and avoid the expiring contract.",
        "",
        "### 2. Guar seed is a reverse-arb market; jeera is two-sided",
        "",
        f"Guar seed futures sat below fair value on {share('GUARSEED', SIGNAL_REVERSE):.0f}% of days and cash-and-carry made money after all costs on only "
        f"{profitable('GUARSEED'):.0f}% {ASSUMED}. Jeera split almost evenly: a tradeable cash-and-carry on {tradeable('JEERA', SIGNAL_CASH_AND_CARRY):.0f}% of days "
        f"and a tradeable reverse arb on {tradeable('JEERA', SIGNAL_REVERSE):.0f}% {ASSUMED}. "
        "The reverse side persists because it cannot be arbitraged from scratch: physical goods cannot be borrowed and sold short, "
        "so only someone already holding stock can sell spot and buy futures. A discount in futures is a payment to stockists, not free money.",
        "",
        "### 3. The model is tight where it can be checked, and size is the real limit",
        "",
        f"Final settlement prices match the 3-day average of the spot series to within {settlements['convergence_error_pct'].abs().mean():.2f}% on average "
        f"across {len(settlements)} expired contracts, and the spot series matched the exchange's spot page exactly on the date checked by hand. "
        "So a position held to delivery converges as modelled; the residual risk is quality, delivery logistics and the 3-day averaging window, not price. "
        f"What limits the trade is liquidity: at 10% of average volume the active contract absorbs about Rs {capacity['JEERA']:.1f} crore a day in jeera "
        f"and Rs {capacity['GUARSEED']:.1f} crore in guar seed. Fixed costs matter too: assaying and handling are paid once, so they weigh heavily on "
        "a 10-day trade and little on a 60-day one.",
        "",
    ]


def write_report(tables: dict[str, pd.DataFrame], tbill: pd.DataFrame) -> Path:
    commodities = settings.load_commodities()
    rbi_fill_days = int(tables["economics"].drop_duplicates("date")["tbill_is_rbi_fill"].sum())
    lines = [
        "# Phase 3: metrics",
        "",
        f"Generated by `python -m src.build_metrics` on {date.today()}. Money amounts are Rs per quintal unless stated; "
        "returns are annualised on a 365-day year.",
        "",
        "## What the numbers rest on",
        "",
        "- **Spot is validated.** The bhavcopy `UndrlygPric` matched the NCDEX spot price page exactly on 9 Oct 2026 "
        "(guar seed Jodhpur 7,706.70 at 16:50; jeera Unjha 24,626.00 at 15:19), a manual check. It is the last polled spot of the day.",
        "- **Storage and assaying are official.** NCCL sheet dated 8 Oct 2026 (`data/reference/`): jeera Rs 0.70 per quintal per day "
        "and Rs 50 per quintal assaying; guar seed Rs 0.45 and Rs 15. Storage includes insurance and fumigation.",
        "- **Exchange fee, SEBI fee, stamp duty, CTT exemption and GST on brokerage are sourced** (see `config/carry_costs.toml`).",
        f"- **Two assumptions remain**, and every number that depends on them is marked {ASSUMED}:",
        "  1. Handling (loading and unloading): Rs 10 per quintal, a placeholder, not sourced.",
        "  2. GST treatment of warehouse charges: 18% applied to storage and assaying as the conservative case; not verified.",
        "- Brokerage (Rs 10 per lakh) and the funding spread (1% over T-bill) are trader inputs, adjustable in the dashboard.",
        f"- The last {rbi_fill_days} trading days use the RBI auction yield because FBIL had not yet published; they are flagged `tbill_is_rbi_fill`.",
        "- Not modelled: repository and repository-participant charges, minimum holding periods for storage, margin funding on the futures leg, "
        "mandi fees and transport on the spot purchase, and quality premiums or discounts at delivery.",
        "",
        "## How to read the two return measures",
        "",
        "- **Net profit** deducts every cost including funding at T-bill plus the spread. Positive means the trade beats borrowing.",
        "- **Cash-and-carry return** is the annualised return on the cash invested after physical and transaction costs but before funding. "
        "It is the figure to compare with the T-bill yield; funding is left out so it is not counted twice.",
        "- **Raw signals** compare futures with fair value (spot plus funding plus storage) and ignore the one-off assaying and handling costs. "
        "A signal is **tradeable** only if the trade also makes money after all costs. Headlines and the opportunity log use the tradeable signal.",
        "",
    ]
    for commodity, details in commodities.items():
        own_snapshot = tables["snapshot"].loc[tables["snapshot"]["commodity"] == commodity].iloc[0]
        lines += [
            f"## {details['name']} (`{details['futures_symbol']}`, {details['basis_centre']})",
            "",
            to_markdown(key_numbers(commodity, tables)),
            "",
            "Header insights generated from the data:",
            "",
            *[f"- {own_snapshot[f'insight_{number}']}" for number in (1, 2, 3)],
            "",
        ]

    buckets = tables["convergence_buckets"].round(2)
    lines += [
        "## Basis by days to expiry",
        "",
        "Average basis % (spot minus futures) across contracts, on days with open interest. It is negative while futures sit above spot "
        "and turns positive as expiry nears and futures fall below spot.",
        "",
        to_markdown(buckets[["commodity", "dte_bucket", "observations", "contracts", "mean_basis_pct", "mean_abs_basis_pct"]]),
        "",
        f"## Sensitivity to the two remaining assumptions {ASSUMED}",
        "",
        "The same year of data re-run through the cost model with different inputs (this is what the dashboard sliders will do).",
        "",
        to_markdown(sensitivity(tables, tbill)),
        "",
        "## Three findings for the interview",
        "",
        *findings(tables),
        "## Metric tables",
        "",
        to_markdown(pd.DataFrame([{"file": f"data/processed/metrics/{name}.parquet", "rows": len(table)} for name, table in tables.items()])),
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH
