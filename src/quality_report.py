"""Data-quality report for the cleaned tables: reports/phase2_data_quality.md."""

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed; we only save PNG files
import matplotlib.pyplot as plt
import pandas as pd

from src.ncdex import NO_FILE_LOG, RAW_BHAVCOPY_DIR, bhavcopy_path

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
OUTLIER_MOVE_PCT = 8.0

SPOT_COLOUR, FUTURES_COLOUR = "#2a78d6", "#eb6834"
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


def to_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_None._"
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    divider = "|" + "---|" * len(frame.columns)
    rows = ["| " + " | ".join(map(str, row)) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *rows])


def as_dates(frame: pd.DataFrame, *columns: str) -> pd.DataFrame:
    """Show datetime columns as plain dates in the report."""
    frame = frame.copy()
    for column in columns:
        frame[column] = frame[column].dt.date
    return frame


def near_month(futures: pd.DataFrame) -> pd.DataFrame:
    """The near-month contract on each date, with its basis against spot."""
    near = futures[futures["contract_rank"] == 1].copy()
    near["basis"] = near["settlement_price"] - near["spot"]
    near["basis_pct"] = near["basis"] / near["spot"] * 100
    return near


def coverage_section(start: date, end: date) -> list[str]:
    weekdays = [d for d in pd.date_range(start, end).date if d.weekday() < 5]
    fetched = [d for d in weekdays if bhavcopy_path(d).exists()]
    no_file_log = RAW_BHAVCOPY_DIR / NO_FILE_LOG
    holidays = set(no_file_log.read_text().split()) if no_file_log.exists() else set()
    missing = [d for d in weekdays if d not in fetched]
    confirmed_holidays = [d for d in missing if d.isoformat() in holidays]
    unexplained = [d for d in missing if d.isoformat() not in holidays]
    return [
        "## 1. Coverage",
        "",
        f"- Window: {start} to {end}",
        f"- Weekdays in the window (expected at most): {len(weekdays)}",
        f"- Trading days fetched: {len(fetched)}",
        f"- Weekdays where NCDEX published no file (exchange holidays): {len(confirmed_holidays)}: "
        + (", ".join(map(str, confirmed_holidays)) or "none"),
        f"- Weekdays not fetched for any other reason: {len(unexplained)}: "
        + (", ".join(map(str, unexplained)) or "none")
        + (" (recent dates may simply not be published yet)" if unexplained else ""),
        "- Weekends are not requested. A special weekend session, if one happened, would be missed.",
        "",
    ]


def row_count_section(futures: pd.DataFrame, spot: pd.DataFrame, tbill: pd.DataFrame) -> list[str]:
    per_commodity = (
        futures.groupby("commodity")
        .agg(
            rows=("date", "size"),
            trading_days=("date", "nunique"),
            contracts=("contract_symbol", "nunique"),
            first_date=("date", "min"),
            last_date=("date", "max"),
            zero_oi_rows=("zero_oi_flag", "sum"),
        )
        .reset_index()
    )
    per_contract = (
        futures.groupby(["commodity", "contract_symbol", "expiry"])
        .agg(
            rows=("date", "size"),
            first_date=("date", "min"),
            last_date=("date", "max"),
            days_with_oi=("zero_oi_flag", lambda flag: int((~flag).sum())),
            peak_oi_mt=("open_interest", "max"),
        )
        .reset_index()
        .sort_values(["commodity", "expiry"])
    )
    sources = tbill["source"].value_counts().rename_axis("source").reset_index(name="days")
    return [
        "## 2. Row counts",
        "",
        f"- `futures.parquet`: {len(futures)} rows; `spot.parquet`: {len(spot)} rows; `tbill.parquet`: {len(tbill)} rows",
        "",
        to_markdown(as_dates(per_commodity, "first_date", "last_date")),
        "",
        "Per contract (`days_with_oi` = days the contract had open interest):",
        "",
        to_markdown(as_dates(per_contract, "expiry", "first_date", "last_date")),
        "",
        f"T-bill yield by source (range {tbill['yield_pct'].min():.2f}% to {tbill['yield_pct'].max():.2f}%):",
        "",
        to_markdown(sources),
        "",
    ]


def spot_consistency_section(futures: pd.DataFrame, mismatches: pd.DataFrame) -> list[str]:
    checked = futures.groupby(["date", "commodity"]).ngroups
    lines = ["## 3. Spot consistency", ""]
    if mismatches.empty:
        lines.append(
            f"**Passed.** `UndrlygPric` is identical across all contracts of a commodity on all {checked} date x commodity pairs."
        )
    else:
        lines += [
            f"**{len(mismatches)} of {checked} date x commodity pairs have contracts that disagree on the spot price.** "
            "The spot table uses the near-month contract's value.",
            "",
            to_markdown(as_dates(mismatches, "trade_date")),
        ]
    lines += [
        "",
        "Manual cross-check against the NCDEX spot price page for 9 Oct 2026: guar seed Jodhpur 7,706.70 (16:50) and "
        "jeera Unjha 24,626.00 (15:19), both identical to the bhavcopy. `UndrlygPric` is the last polled spot of the day.",
    ]
    return lines + [""]


def convergence_table(futures: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """Final settlement price of each expired contract vs spot at expiry.

    Compared with spot on the expiry day and with the average spot of the last
    three trading days, which is how the exchange appears to set the final price.
    """
    spot = spot.sort_values(["commodity", "date"]).copy()
    spot["spot_3day_avg"] = spot.groupby("commodity")["spot"].transform(lambda prices: prices.rolling(3).mean())

    last_date = futures["date"].max()
    expired = futures[futures["expiry"] <= last_date]
    final = expired.sort_values("date").groupby(["commodity", "contract_symbol"], as_index=False).last()
    final = final.merge(spot[["date", "commodity", "spot_3day_avg"]], on=["date", "commodity"], how="left")
    final["gap_vs_spot_pct"] = ((final["settlement_price"] / final["spot"] - 1) * 100).round(3)
    final["gap_vs_3day_avg_pct"] = ((final["settlement_price"] / final["spot_3day_avg"] - 1) * 100).round(3)
    final["spot_3day_avg"] = final["spot_3day_avg"].round(2)
    final["on_expiry_day"] = final["date"] == final["expiry"]
    columns = [
        "commodity", "contract_symbol", "expiry", "on_expiry_day", "settlement_price",
        "spot", "gap_vs_spot_pct", "spot_3day_avg", "gap_vs_3day_avg_pct",
    ]  # fmt: skip
    return final[columns].sort_values(["commodity", "expiry"])


def convergence_section(futures: pd.DataFrame, spot: pd.DataFrame) -> list[str]:
    table = convergence_table(futures, spot)
    same_day = table["gap_vs_spot_pct"].abs()
    three_day = table["gap_vs_3day_avg_pct"].abs()
    return [
        "## 4. Expiry convergence",
        "",
        "For every contract that expired inside the window: its final settlement price against the spot price on expiry "
        "day, and against the average spot of the last three trading days (expiry day and the two before).",
        "",
        f"- Contracts checked: {len(table)}; all have a row on their expiry day: {bool(table['on_expiry_day'].all())}",
        f"- Gap to same-day spot: mean {same_day.mean():.3f}%, largest {same_day.max():.3f}%",
        f"- Gap to 3-day average spot: mean {three_day.mean():.3f}%, largest {three_day.max():.3f}%",
        "",
        "**Reading.** The final settlement price tracks the 3-day average of `UndrlygPric` far more closely than the "
        "same-day value, often to the paisa. That pattern says the exchange settles contracts on a 3-day average of this "
        "same spot series, which confirms `UndrlygPric` is the delivery-centre spot. The gaps to same-day spot are the "
        "averaging effect when spot is moving, not a data problem. (The 3-day rule is inferred from the data; the "
        "contract specification should be read to confirm it.)",
        "",
        to_markdown(as_dates(table, "expiry")),
        "",
    ]


def outlier_section(futures: pd.DataFrame, spot: pd.DataFrame) -> list[str]:
    spot = spot.sort_values(["commodity", "date"]).copy()
    spot["move_pct"] = (spot.groupby("commodity")["spot"].pct_change() * 100).round(2)
    spot_jumps = spot[spot["move_pct"].abs() > OUTLIER_MOVE_PCT][["date", "commodity", "spot", "move_pct"]]

    # Day-on-day change of the same contract, so the roll to a new near month is not counted as a jump.
    by_contract = futures.sort_values(["contract_symbol", "date"]).copy()
    by_contract["move_pct"] = (by_contract.groupby("contract_symbol")["settlement_price"].pct_change() * 100).round(2)
    near_jumps = by_contract[(by_contract["contract_rank"] == 1) & (by_contract["move_pct"].abs() > OUTLIER_MOVE_PCT)]
    near_jumps = near_jumps[["date", "commodity", "contract_symbol", "settlement_price", "move_pct"]]

    bad_prices = futures[(futures["settlement_price"] <= 0) | (futures["spot"] <= 0)]
    untraded = futures[futures["volume"] == 0]
    return [
        "## 5. Outliers",
        "",
        f"Spot moves larger than {OUTLIER_MOVE_PCT:.0f}% in a day:",
        "",
        to_markdown(as_dates(spot_jumps, "date")),
        "",
        f"Near-month settlement moves larger than {OUTLIER_MOVE_PCT:.0f}% in a day (same contract, day on day):",
        "",
        to_markdown(as_dates(near_jumps, "date")),
        "",
        f"- Rows with a zero or negative settlement or spot price: {len(bad_prices)}",
        f"- Rows with no trades that day: {len(untraded)} of {len(futures)}. Their open/high/low are 0 by design, "
        "so use `settlement_price` (never OHLC) for untraded contracts.",
        "",
    ]


def quick_stats_section(futures: pd.DataFrame) -> list[str]:
    near = near_month(futures)
    rows = []
    for commodity, group in near.groupby("commodity"):
        latest = group.sort_values("date").iloc[-1]
        rows.append(
            {
                "commodity": commodity,
                "spot_min": group["spot"].min(),
                "spot_max": group["spot"].max(),
                "basis_pct_min": round(group["basis_pct"].min(), 2),
                "basis_pct_mean": round(group["basis_pct"].mean(), 2),
                "basis_pct_max": round(group["basis_pct"].max(), 2),
                "days_futures_above_spot_pct": round((group["basis"] > 0).mean() * 100, 1),
                "days_futures_below_spot_pct": round((group["basis"] < 0).mean() * 100, 1),
                "current_basis": round(latest["basis"], 2),
                "current_basis_pct": round(latest["basis_pct"], 2),
                "as_of": latest["date"].date(),
            }
        )
    return [
        "## 6. Quick stats (near-month contract)",
        "",
        "Basis = near-month settlement price minus spot, in Rs/quintal and as % of spot. "
        "Futures above spot is the contango (cash-and-carry) side; below spot is backwardation (reverse arb).",
        "",
        to_markdown(pd.DataFrame(rows)),
        "",
    ]


def save_charts(futures: pd.DataFrame) -> list[str]:
    """One PNG per commodity: spot against the near-month settlement price."""
    near = near_month(futures)
    lines = ["## 7. Sanity charts", ""]
    for commodity, group in near.groupby("commodity"):
        figure, axis = plt.subplots(figsize=(9, 3.6), dpi=110, facecolor=SURFACE)
        axis.set_facecolor(SURFACE)
        axis.plot(group["date"], group["spot"], color=SPOT_COLOUR, linewidth=2, label="Spot")
        axis.plot(group["date"], group["settlement_price"], color=FUTURES_COLOUR, linewidth=2, label="Near-month futures")
        axis.set_title(f"{commodity}: spot vs near-month futures (Rs/quintal)", loc="left", color=INK, fontsize=11)
        axis.grid(axis="y", color="#e4e3df", linewidth=0.8)
        axis.tick_params(colors=MUTED, labelsize=9, length=0)
        for side in ("top", "right", "left"):
            axis.spines[side].set_visible(False)
        axis.spines["bottom"].set_color("#c9c8c2")
        axis.legend(frameon=False, fontsize=9, labelcolor=INK, loc="lower right", ncols=2)
        figure.tight_layout()
        file_name = f"phase2_{commodity.lower()}_spot_vs_near.png"
        figure.savefig(REPORTS_DIR / file_name)
        plt.close(figure)
        lines += [f"![{commodity} spot vs near-month futures]({file_name})", ""]
    return lines


def write_report(
    futures: pd.DataFrame, spot: pd.DataFrame, tbill: pd.DataFrame, spot_mismatches: pd.DataFrame, start: date, end: date
) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    lines = [
        "# Phase 2: data quality report",
        "",
        f"Generated by `python -m src.pipeline` on {date.today()}. Prices in Rs/quintal; volume and open interest in MT.",
        "",
        *coverage_section(start, end),
        *row_count_section(futures, spot, tbill),
        *spot_consistency_section(futures, spot_mismatches),
        *convergence_section(futures, spot),
        *outlier_section(futures, spot),
        *quick_stats_section(futures),
        *save_charts(futures),
    ]
    report_path = REPORTS_DIR / "phase2_data_quality.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
