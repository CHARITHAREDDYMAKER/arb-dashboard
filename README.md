# Commodity Cash-Futures Arbitrage Monitor

A dashboard that measures the cash-and-carry arbitrage in two physically
delivered NCDEX agri commodities, jeera (cumin) and guar seed, over the past
year, after real warehouse, exchange and funding costs.

**Live app:** _add the Streamlit link here after deploying (see [DEPLOY.md](DEPLOY.md))_

![Overview tab](reports/screens/jeera_0_overview.png)

## The trade

Buy the commodity in the spot market, store it in an NCDEX-accredited
warehouse, sell futures against it and deliver at expiry. The return is the
basis (futures minus spot) less the cost of carrying the goods, and it is worth
doing when it beats the 91-day T-bill. When futures trade below fair value the
trade runs in reverse, for someone who already holds stock.

| Commodity | Futures symbol | Spot / basis centre |
|---|---|---|
| Jeera (cumin) | `JEERAUNJHA` | Unjha |
| Guar seed | `GUARSEED10` | Jodhpur (Bikaner is an additional delivery centre, at a discount) |

## What it found (10 Oct 2025 to 9 Oct 2026)

1. **The carry is earned early, not near expiry.** Both futures curves slope up about 1.4% a month, but a contract loses its premium as delivery approaches: jeera averages +2.0% over spot 46 to 60 days out and -0.5% at 6 to 10 days.
2. **Guar seed is a reverse-arb market; jeera is two-sided.** Guar seed paid stock holders on 64% of days and cash-and-carry on 14%. Jeera split 44% and 43%.
3. **The model is tight; size is the limit.** Contracts settled within 0.07% of the 3-day average spot, but only about Rs 3 crore (jeera) and Rs 11 crore (guar seed) a day is tradeable at 10% of volume.

The tradeable-day figures depend on two assumptions (handling cost, GST on
warehouse charges), which can be changed in the app. See
[reports/phase3_metrics.md](reports/phase3_metrics.md) and
[reports/interview_notes.md](reports/interview_notes.md).

## Run it locally

Requires Python 3.11 or newer (built and tested on 3.13).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app/app.py
```

The sidebar picks the commodity and date range and holds the desk inputs
(brokerage, funding spread, handling, GST on storage, cold storage, signal
threshold, roll rule). Changing an input re-runs the cost model on the whole
year. The header always shows the latest trading day.

| Tab | Question it answers |
|---|---|
| Overview | What does the desk do, and what are the three findings? |
| Signal Finder | What does each trade earn on any date, and which dates were best for each side? |
| Cash vs Futures | How far do futures trade from spot, and is the latest basis unusual? |
| Arb Economics | Does cash-and-carry beat the T-bill, what does reverse arb pay, and where do the costs go? |
| Term Structure | What does the curve look like, and is the market paying to carry stock forward? |
| Convergence & Entry Timing | Does a contract keep its premium into delivery, and when should the trade be entered? |
| Rollover & Capacity | What does the roll cost, and how much size can be traded? |
| Method & Data | The trade, the data sources, the validation evidence and the cost model. |

The dashboard is dark by default; a toggle in the sidebar switches to a light
theme. Screenshots of every tab are in [reports/screens/](reports/screens/).

A full guide to every chart, card and table, with worked examples, is in
[docs/Dashboard_Guide.md](docs/Dashboard_Guide.md) (also as a Word file,
`docs/Dashboard_Guide.docx`).

## Data

All data comes from free, public sources. Nothing here is proprietary, and no
credentials are needed to reproduce it.

| Data | Source |
|---|---|
| Futures settlement price, open interest, volume, expiry dates, spot price | [NCDEX](https://www.ncdex.com/markets/bhavcopy) daily bhavcopy |
| 91-day T-bill yield | [FBIL](https://www.fbil.org.in/) T-bill curve (daily); [RBI](https://www.rbi.org.in/) auction yield for the latest days |
| Storage and assaying charges | NCCL "Storage and Assaying Charges", 8 Oct 2026 (copy in `data/reference/`) |
| Exchange fee, SEBI fee, stamp duty, CTT | NCDEX, SEBI and NSE publications, linked in `config/carry_costs.toml` |

The data belongs to its publishers and is reproduced here for analysis only.
This project is not affiliated with NCDEX, NCCL, FBIL or RBI, and nothing in it
is investment advice.

**Is the spot price right?** Spot is the bhavcopy's `UndrlygPric`, the last
polled spot of the day at the basis centre. It matched the NCDEX spot price page
exactly on 9 Oct 2026 (jeera Unjha 24,626.00, guar seed Jodhpur 7,706.70; one
date, checked by hand), and across 23 expired contracts the final settlement
price equals the 3-day average of this series to within 0.07% on average.

## Refresh the data

```powershell
pip install -r requirements-dev.txt
python -m src.pipeline --update     # fetch new trading days, rebuild the tables
python -m src.build_metrics         # rebuild the metric tables and the Phase 3 report
python -m pytest                    # unit tests
```

A first full download (`python -m src.pipeline`) takes about 25 minutes: one
polite request per trading day, cached in `data/raw/bhavcopy/`.

| File | One row per | Key columns |
|---|---|---|
| `data/processed/futures.parquet` | date x contract | settlement price, volume, open interest, spot, days to expiry, `contract_rank`, `zero_oi_flag` |
| `data/processed/spot.parquet` | date x commodity | spot price at the basis centre |
| `data/processed/tbill.parquet` | trading date | 91-day T-bill yield and its origin (`fbil`, `ffill`, `rbi_fill`) |

Prices are in Rs per quintal; volume and open interest in MT.

## How the code is organised

```
app/        Streamlit dashboard: app.py, sidebar, header, one module per tab in tabs/
src/        data pipeline (ncdex, rates, build_tables, pipeline) and metrics engine (metrics, build_metrics)
config/     commodities.toml, carry_costs.toml (published charges with sources), assumptions.toml (desk inputs and rules)
tests/      unit tests, plus browser_check.py and take_screenshots.py (Playwright)
data/       processed/ parquet files (committed), reference/ source documents, raw/ downloads (not committed)
reports/    phase reports, interview notes, screenshots
docs/       the dashboard guide (Markdown and Word) and the script that builds the Word file
```

[src/metrics.py](src/metrics.py) holds pure functions with no file or network
access. The cost model takes a `CostInputs` object, so the dashboard re-runs a
year of data in about 10 milliseconds when an input changes (redrawing the
page takes a second or two). The app contains
no metric logic of its own.

## Cost inputs

| Status | Inputs |
|---|---|
| Confirmed from a published source | Storage and assaying (NCCL), exchange fee (NCDEX), SEBI fee, stamp duty, CTT exemption, GST on brokerage |
| Desk input | Brokerage, funding spread over T-bill |
| Assumption | Handling (Rs 10 per quintal, not published); 18% GST on storage and assaying (treatment not verified) |

Not modelled: repository charges, minimum storage periods, margin funding,
mandi fees and transport, quality premiums and discounts.

## Licence

MIT. See [LICENSE](LICENSE).
