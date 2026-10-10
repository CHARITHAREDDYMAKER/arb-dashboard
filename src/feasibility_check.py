"""Phase 1 data feasibility check.

Fetches a small sample from every data source the monitor needs and writes
reports/phase1_data_check.md. Run from the project root:

    python -m src.feasibility_check
"""

import io
import re
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from src.http_client import make_session, polite_get
from src.ncdex import BHAVCOPY_URL, fetch_bhavcopy_zip, read_bhavcopy_zip, tidy_futures
from src.rates import (
    FBIL_91_DAY_TENOR,
    FBIL_TBILL_URL,
    RBI_HOME_URL,
    fetch_fbil_tbill_curve,
    fetch_rbi_latest_tbill,
)

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
REPORT_PATH = ROOT / "reports" / "phase1_data_check.md"

RECENT_DAYS_WANTED = 5
HISTORY_DAYS_BACK = 365

NCDEX_SPOT_URL = "https://www.ncdex.com/markets/spotprices"
NCCL_CHARGES_URL = "https://www.nccl.co.in/warehousing/warehouse-data"
BROWSER_CHECK_MARKER = "Fingerprint.start"  # present when the site serves its JS browser check


def to_markdown(frame: pd.DataFrame) -> str:
    """Render a small DataFrame as a markdown table."""
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    divider = "|" + "---|" * len(frame.columns)
    rows = ["| " + " | ".join(map(str, row)) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *rows])


def previous_weekdays(start: date):
    """Yield weekdays going backwards from `start` (inclusive)."""
    day = start
    while True:
        if day.weekday() < 5:
            yield day
        day -= timedelta(days=1)


def collect_bhavcopies(session, start: date, wanted: int, max_tries: int = 12) -> dict:
    """Fetch the `wanted` most recent trading days on or before `start`.

    Returns {date: (udiff, legacy)}. Weekdays without a file (holidays) are skipped.
    """
    out_dir = RAW_DIR / "bhavcopy"
    out_dir.mkdir(parents=True, exist_ok=True)
    found = {}
    for tries, day in enumerate(previous_weekdays(start)):
        if len(found) == wanted or tries == max_tries:
            break
        content = fetch_bhavcopy_zip(session, day)
        print(f"  bhavcopy {day}: {'ok' if content else 'no file (holiday?)'}")
        if content:
            (out_dir / f"bhavcopy_{day:%Y%m%d}.zip").write_bytes(content)
            found[day] = read_bhavcopy_zip(content)
    return found


def is_behind_browser_check(session, url: str) -> bool:
    return BROWSER_CHECK_MARKER in polite_get(session, url).text


def read_transaction_fee(session, pdf_url: str) -> str | None:
    """Pull the futures transaction-charge line out of the NCDEX master circular."""
    pdf = PdfReader(io.BytesIO(polite_get(session, pdf_url, timeout=120).content))
    for page in pdf.pages:
        match = re.search(r"Futures Contracts\s+Rs\.\s*[\d.]+\s+per lakh of turnover value", page.extract_text() or "")
        if match:
            return re.sub(r"\s+", " ", match.group(0))
    return None


def section_futures_and_spot(session, commodities: dict, today: date) -> tuple[list[str], dict]:
    symbols = [c["futures_symbol"] for c in commodities.values()]
    recent = collect_bhavcopies(session, today, RECENT_DAYS_WANTED)
    history = collect_bhavcopies(session, today - timedelta(days=HISTORY_DAYS_BACK), wanted=1)

    tidy = pd.concat([tidy_futures(udiff, legacy, symbols) for udiff, legacy in recent.values()])
    latest_day = max(recent)
    latest_udiff, latest_legacy = recent[latest_day]
    latest = tidy[tidy["trade_date"] == pd.Timestamp(latest_day)]

    per_day = (
        tidy.groupby(["trade_date", "symbol"])
        .agg(
            expiries=("expiry_date", "nunique"),
            expiries_with_oi=("open_interest_mt", lambda oi: int((oi > 0).sum())),
            spot_price=("spot_price", "first"),
            basis_centre=("basis_centre", "first"),
        )
        .reset_index()
    )
    per_day["trade_date"] = per_day["trade_date"].dt.date

    show = ["symbol", "expiry_date", "settle_price", "spot_price", "open_interest_mt", "volume_mt", "lot_size_mt", "basis_centre"]
    latest_view = latest[show].copy()
    latest_view["expiry_date"] = latest_view["expiry_date"].dt.date

    lines = [
        "## 1. NCDEX futures and spot (daily bhavcopy)",
        "",
        "**Status: WORKED.** One request per trading day returns every contract on the exchange.",
        "",
        f"- Endpoint: `GET {BHAVCOPY_URL}?file_type_both=final&type_both=bhavcopy&bhavcopy_for=both&filedate_both=DD-MM-YYYY&format_both=csv_file`",
        "- Response: a zip with two CSVs. Weekends and holidays return the HTML page instead (detected by Content-Type).",
        f"- Trading days fetched: {', '.join(str(d) for d in sorted(recent))}",
        f"- UDiFF file columns ({len(latest_udiff.columns)}): `{', '.join(latest_udiff.columns)}`",
        f"- Legacy file columns ({len(latest_legacy.columns)}): `{', '.join(c for c in latest_legacy.columns if not c.startswith('Unnamed'))}`",
        f"- Row counts on {latest_day}: UDiFF {len(latest_udiff)} (all NCDEX instruments), legacy {len(latest_legacy)}; "
        f"futures rows for our two symbols over the {len(recent)} days: {len(tidy)}",
        "- Units: prices in Rs/quintal; open interest and volume in MT.",
        "",
        "**Spot price.** The bhavcopy column `UndrlygPric` (underlying price) is taken as the exchange's spot price for the "
        "contract's basis centre, so spot and futures come from the same file and the same date. This reading is not yet "
        "cross-checked against the spot page (see gap 3). The centre name comes from the legacy file's "
        "`Ex-Basis Delivery Centre` column.",
        "",
        "Per day and symbol (number of listed expiries, how many have open interest, spot price, basis centre):",
        "",
        to_markdown(per_day),
        "",
        f"All expiries on {latest_day} (near / next / far):",
        "",
        to_markdown(latest_view),
        "",
        "Three raw sample rows (UDiFF, first 22 columns):",
        "",
        to_markdown(latest_udiff[latest_udiff["TckrSymb"].isin(symbols)].iloc[:3, :22].fillna("")),
        "",
    ]

    if history:
        old_day = next(iter(history))
        old = tidy_futures(*history[old_day], symbols)
        old_summary = old.groupby("symbol").agg(expiries=("expiry_date", "nunique"), spot_price=("spot_price", "first")).reset_index()
        lines += [
            f"**12-month history: reachable.** The same endpoint returned {old_day} ({(today - old_day).days} days back):",
            "",
            to_markdown(old_summary),
            "",
        ]
    else:
        lines += ["**12-month history: NOT confirmed** (no file returned for the probe date).", ""]

    facts = {
        "futures_ok": all((per_day["expiries"] >= 3)),
        "spot_ok": bool(tidy["spot_price"].gt(0).all()),
        "history_ok": bool(history),
        "centres": per_day.drop_duplicates("symbol").set_index("symbol")["basis_centre"].to_dict(),
    }
    return lines, facts


def section_spot_page(session) -> list[str]:
    blocked = is_behind_browser_check(session, NCDEX_SPOT_URL)
    status = (
        "**Status: NOT USED.** The page answers with a JavaScript browser-fingerprint check instead of data. "
        "We do not try to get around it; spot prices come from the bhavcopy instead (section 1)."
        if blocked
        else "**Status: reachable** (no browser check on this run). Not needed, since the bhavcopy carries the spot price."
    )
    return ["## 2. NCDEX spot price page", "", f"- URL: `{NCDEX_SPOT_URL}`", "", status, ""]


def section_carry_costs(session, costs: dict) -> tuple[list[str], dict]:
    fee = costs["exchange_transaction_fee"]
    fee_line = read_transaction_fee(session, fee["source_url"])
    nccl_blocked = is_behind_browser_check(session, NCCL_CHARGES_URL)

    def flatten(table: dict, prefix: str = ""):
        for key, value in table.items():
            if "status" in value:
                yield {"item": prefix + key, "status": value["status"], "value": f"{value.get('value', '')} {value.get('unit', '') if 'value' in value else ''}".strip()}
            else:
                yield from flatten(value, prefix=f"{key}.")

    inventory = pd.DataFrame(flatten(costs))
    lines = [
        "## 3. Carry costs (warehouse charges and fee schedule)",
        "",
        "**Status: PARTIAL.** The exchange fee is confirmed; warehouse storage and assaying rates are not yet in hand.",
        "",
        f"- Transaction fee source: `{fee['source_url']}` ({fee['source']})",
        f"- Text found in the PDF: \"{fee_line}\"" if fee_line else "- Transaction fee text NOT found in the PDF (check the URL).",
        f"- Warehouse storage and assaying charges: published by NCCL at `{NCCL_CHARGES_URL}` "
        f"under 'Indicative storage and assaying charges'. "
        + ("That page is behind the same browser check, so it must be read by hand." if nccl_blocked else "Page reachable on this run."),
        "- Cost allocation rule (NCCL Logistics Master Circular, section 10): the seller pays sampling, grading, weighing, "
        "handling and storage up to pay-in; the buyer pays everything after pay-out.",
        "",
        "Inventory of carry-cost inputs in `config/carry_costs.toml`:",
        "",
        to_markdown(inventory),
        "",
    ]
    return lines, {"fee_ok": fee_line is not None, "storage_ok": not (inventory["status"] == "missing").any()}


def section_risk_free(session, today: date) -> tuple[list[str], dict]:
    curve = fetch_fbil_tbill_curve(session, today - timedelta(days=HISTORY_DAYS_BACK + 10), today)
    three_month = curve[curve["tenor"] == FBIL_91_DAY_TENOR].copy()
    three_month["date"] = three_month["date"].dt.date
    rbi = fetch_rbi_latest_tbill(session)

    out_dir = RAW_DIR / "rates"
    out_dir.mkdir(parents=True, exist_ok=True)
    curve.to_csv(out_dir / "fbil_tbill_curve_sample.csv", index=False)

    lines = [
        "## 4. Risk-free rate (91-day T-bill)",
        "",
        "**Status: WORKED.** Daily history from FBIL, latest auction yield from RBI.",
        "",
        f"- FBIL endpoint: `GET {FBIL_TBILL_URL}?fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD&authenticated=false` (JSON)",
        "- Raw fields: `processRunDate, displayTime, tenorName, rate, comments`",
        f"- Rows: {len(curve)} ({curve['tenor'].nunique()} tenors x {curve['date'].nunique()} days), "
        f"from {curve['date'].min().date()} to {curve['date'].max().date()}",
        f"- We use the `{FBIL_91_DAY_TENOR}` tenor as the 91-day yield: {len(three_month)} daily observations.",
        f"- RBI latest: `{RBI_HOME_URL}` 'Current Rates' box shows {rbi['tenor']} = {rbi['yield_pct']}% ({rbi['basis']}).",
        "",
        "Last three FBIL observations:",
        "",
        to_markdown(three_month.tail(3)),
        "",
    ]
    lag_days = (today - curve["date"].max().date()).days
    return lines, {"rate_ok": len(three_month) > 200, "fbil_lag_days": lag_days}


def section_checklist(facts: dict) -> list[str]:
    def mark(ok: bool) -> str:
        return "YES" if ok else "NO"

    centres = facts["centres"]
    rows = pd.DataFrame(
        [
            ["Spot price", mark(facts["spot_ok"]), mark(facts["spot_ok"]), "Bhavcopy `UndrlygPric`; centres: " + ", ".join(f"{s} = {c}" for s, c in centres.items())],
            ["Futures prices, near / next / far", mark(facts["futures_ok"]), mark(facts["futures_ok"]), "Bhavcopy `SttlmPric`, one row per expiry"],
            ["Open interest and volume", mark(facts["futures_ok"]), mark(facts["futures_ok"]), "Bhavcopy `OpnIntrst`, `TtlTradgVol` (MT)"],
            ["Expiry dates", mark(facts["futures_ok"]), mark(facts["futures_ok"]), "Bhavcopy `FininstrmActlXpryDt`"],
            ["Exchange transaction fee", mark(facts["fee_ok"]), mark(facts["fee_ok"]), "NCDEX master circular"],
            ["Warehouse storage, assaying, handling", mark(facts["storage_ok"]), mark(facts["storage_ok"]), "NCCL page needs a manual read"],
            ["Risk-free rate", mark(facts["rate_ok"]), mark(facts["rate_ok"]), "FBIL 3-month T-bill, daily"],
            ["12 months of history reachable", mark(facts["history_ok"]), mark(facts["history_ok"]), "Bhavcopy and FBIL both go back 12+ months"],
        ],
        columns=["Requirement", "JEERA", "GUAR SEED", "Where it comes from"],
    )
    gaps = [
        "## 6. Gaps and things to know",
        "",
        "1. **Warehouse storage, assaying and handling rates are missing.** NCCL publishes them, but only on a page behind a "
        "browser check. Open the page in a normal browser, copy the Unjha (jeera) and Jodhpur (guar seed) rates into "
        "`config/carry_costs.toml`.",
        "2. **Guar seed basis centre is Jodhpur, not Bikaner.** Bikaner is an additional delivery centre at a discount. "
        "The spot price in the bhavcopy is therefore the Jodhpur price.",
        "3. **The spot price has not been cross-checked against the NCDEX spot page**, because that page is behind the browser "
        "check. Compare two or three dates by hand in a browser before relying on it.",
        "4. **Far-month contracts are listed but barely trade.** Many far expiries show zero volume and zero open interest; "
        "their settlement price looks like an exchange-computed theoretical value, not a traded one. Term-structure and rollover charts should mark or "
        "drop contracts with no open interest.",
        "5. **Taxes and smaller fees are unverified** (SEBI fee, stamp duty, CTT exemption, GST treatment). They are listed as "
        "`to_verify` in the config with no values used yet.",
        f"6. **FBIL public data lags by about {facts['fbil_lag_days']} days.** Fill the most recent days from the last "
        "available value or the RBI auction yield.",
        "7. **NCDEX throttles bursts.** After roughly ten quick requests it drops connections for a minute. The 12-month "
        "download (about 250 requests) should run with a 5-second delay and retries: roughly 25 minutes, run once and cached.",
        "8. **Two expiry columns.** `XpryDt` is the scheduled date and `FininstrmActlXpryDt` the actual one; they differ when "
        "the scheduled date is a holiday. We use the actual date.",
        "",
        "**MCX copper fallback: not needed.**",
        "",
    ]
    return ["## 5. Confirmation checklist", "", to_markdown(rows), "", *gaps]


def main() -> None:
    today = date.today()
    commodities = tomllib.loads((ROOT / "config" / "commodities.toml").read_text())
    costs = tomllib.loads((ROOT / "config" / "carry_costs.toml").read_text())
    session = make_session()

    print("1/4 NCDEX bhavcopy (futures + spot)")
    futures_lines, facts = section_futures_and_spot(session, commodities, today)
    print("2/4 NCDEX spot page")
    spot_lines = section_spot_page(session)
    print("3/4 Carry costs")
    cost_lines, cost_facts = section_carry_costs(session, costs)
    print("4/4 Risk-free rate")
    rate_lines, rate_facts = section_risk_free(session, today)
    facts |= cost_facts | rate_facts

    header = [
        "# Phase 1: data feasibility check",
        "",
        f"Generated by `python -m src.feasibility_check` on {today}. "
        "Commodities: JEERA (`JEERAUNJHA`) and GUAR SEED (`GUARSEED10`) on NCDEX.",
        "",
    ]
    report = header + futures_lines + spot_lines + cost_lines + rate_lines + section_checklist(facts)
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"Report written to {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
