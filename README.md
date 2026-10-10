# Commodity Cash-Futures Arbitrage Monitor

A dashboard that tracks the cash-and-carry arbitrage in two physically delivered
NCDEX agri commodities over the last 12 months:

| Commodity | Futures symbol | Spot / basis centre |
|---|---|---|
| Jeera (cumin), primary | `JEERAUNJHA` | Unjha |
| Guar seed, secondary | `GUARSEED10` | Jodhpur (Bikaner is an additional delivery centre, at a discount) |

**The trade.** Buy the commodity in the spot market, store it in an
NCDEX-accredited warehouse, sell futures, and deliver at expiry. The return is
the basis (futures minus spot) less the cost of carrying the goods. It is worth
doing when that net return, annualised, beats the 91-day T-bill yield. When
futures trade below spot the trade runs in reverse.

## Run the dashboard

```powershell
streamlit run app/app.py
```

The sidebar picks the commodity and date range and holds the desk inputs
(brokerage, funding spread, handling, GST on storage, cold storage, signal
threshold, roll rule). Changing an input re-runs the cost model on the whole year.

| Tab | Question it answers |
|---|---|
| Cash vs Futures | How far do futures trade from spot, and is today's basis unusual? |
| Arb Economics | Does cash-and-carry beat the T-bill, what does reverse arb pay, and where do the costs go? |
| Term Structure | What does the curve look like, and is the market paying to carry stock forward? |
| Convergence & Entry Timing | Does a contract keep its premium into delivery, and when should the trade be entered? |
| Rollover & Capacity | What does the roll cost, and how much size can be traded? |
| Method & Data | The trade, the data sources, the validation evidence and the cost model. |

The header shows spot, the active contract, basis, the tradeable signal, carry
return against the T-bill, break-even storage, and three sentences generated
from the data. Figures marked with a dagger depend on an assumption.

Screenshots of every tab are in [reports/phase4_screens/](reports/phase4_screens/).
`python tests/take_screenshots.py` regenerates them from a running app.

## Data sources (all free)

| Data | Source |
|---|---|
| Futures settlement price, OI, volume, expiry dates, spot price | NCDEX daily bhavcopy |
| Exchange transaction fee | NCDEX Master Circular, Trading Operations |
| Warehouse storage and assaying charges | NCCL "Indicative storage and assaying charges" |
| 91-day T-bill yield | FBIL T-bill curve (daily), RBI (latest auction) |

Endpoints, sample rows and known gaps are in
[reports/phase1_data_check.md](reports/phase1_data_check.md).

## Project layout

```
config/     commodities.toml, carry_costs.toml (fees and charges, with source links)
src/        data pipeline and metrics engine
tests/      unit tests
app/        Streamlit dashboard: app.py, sidebar, header, one module per tab in tabs/
data/raw/   downloads as received (not committed)
data/processed/   cleaned parquet files used by the dashboard (metrics/ holds the derived tables)
data/reference/   source documents for cost inputs
reports/    phase reports
```

## Setup

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.pipeline              # first run: about 25 minutes (one polite request per trading day)
python -m src.pipeline --update     # later runs: fetch only new dates, rebuild the tables
```

## Data pipeline

`python -m src.pipeline` downloads the NCDEX bhavcopy for every trading day since
10 Oct 2025 (cached in `data/raw/bhavcopy/`, so reruns are fast), then builds:

| File | One row per | Key columns |
|---|---|---|
| `data/processed/futures.parquet` | date x contract | settlement price, volume, open interest, spot, days to expiry, `contract_rank` (1 = near month), `zero_oi_flag` |
| `data/processed/spot.parquet` | date x commodity | spot price at the basis centre |
| `data/processed/tbill.parquet` | trading date | 91-day T-bill yield and where each value came from (`fbil`, `ffill`, `rbi_fill`) |

Prices are in Rs/quintal; volume and open interest in MT. Each run also rewrites
[reports/phase2_data_quality.md](reports/phase2_data_quality.md).

**Is the spot price right?** Spot is the bhavcopy's `UndrlygPric`, the last polled
spot of the day at the basis centre. Two checks support it:

- It matched the NCDEX spot price page exactly on 9 Oct 2026 (jeera Unjha 24,626.00,
  guar seed Jodhpur 7,706.70), checked by hand.
- Across 23 expired contracts the final settlement price equals the 3-day average
  of this series to within 0.07% on average.

## Metrics engine

```powershell
python -m src.build_metrics         # writes data/processed/metrics/*.parquet and reports/phase3_metrics.md
python -m pytest                    # unit tests for the cost model, roll rule and signals
```

[src/metrics.py](src/metrics.py) holds pure functions: basis, cash-and-carry and
reverse-arb economics, fair value and signals, convergence, term structure,
rollover, liquidity and the header snapshot. The cost model is a function of a
`CostInputs` object, so the dashboard can re-run a year of data with the user's
own brokerage, funding spread, storage and handling numbers.

| Config file | Holds |
|---|---|
| `config/carry_costs.toml` | Published charges with sources: exchange fee, SEBI fee, stamp duty, CTT, GST, NCCL storage and assaying |
| `config/assumptions.toml` | Trader inputs and model rules: brokerage, funding spread, handling, roll rule, signal threshold, capacity |
| `data/reference/` | The NCCL storage and assaying charges sheet (8 Oct 2026) |

Two inputs are still assumptions: handling (Rs 10 per quintal, a placeholder) and
whether 18% GST applies to warehouse charges (applied by default as the
conservative case). Key numbers and findings are in
[reports/phase3_metrics.md](reports/phase3_metrics.md).

## Status

- [x] Phase 1: setup and data feasibility check
- [x] Phase 2: data pipeline (12 months, both commodities)
- [x] Phase 3: metrics engine
- [x] Phase 4: Streamlit dashboard
- [ ] Phase 5: polish, insights, deployment
