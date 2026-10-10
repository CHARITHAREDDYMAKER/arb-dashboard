# Commodity Cash-Futures Arbitrage Monitor

A dashboard that tracks the cash-and-carry arbitrage in two physically delivered
NCDEX agri commodities over the last 12 months:

| Commodity | Futures symbol | Spot / basis centre |
|---|---|---|
| Jeera (cumin), primary | `JEERAUNJHA` | Unjha |
| Guar seed, secondary | `GUARSEED10` | Jodhpur |

**The trade.** Buy the commodity in the spot market, store it in an
NCDEX-accredited warehouse, sell futures, and deliver at expiry. The return is
the basis (futures minus spot) less the cost of carrying the goods. It is worth
doing when that net return, annualised, beats the 91-day T-bill yield. When
futures trade below spot the trade runs in reverse.

## What the dashboard will show

1. **Cash vs Futures**: spot and near-month futures, basis in Rs and %, and its distribution.
2. **Cash-and-Carry Return vs Risk-Free**: annualised gross and net-of-cost return against the 91-day T-bill.
3. **Term Structure**: futures prices across expiries; contango vs backwardation through the harvest cycle.
4. **Expiry Convergence**: basis % against days to expiry.
5. **Rollover and Liquidity**: near/next spread and how open interest and volume move in expiry week.

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
src/        data fetching and (later) the metrics engine
app/        Streamlit dashboard (Phase 4)
data/raw/   downloads as received (not committed)
data/processed/   cleaned parquet files used by the dashboard
reports/    phase reports
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.feasibility_check     # writes reports/phase1_data_check.md
```

Requires Python 3.11 or newer.

## Status

- [x] Phase 1: setup and data feasibility check
- [ ] Phase 2: data pipeline (12 months, both commodities)
- [ ] Phase 3: metrics engine
- [ ] Phase 4: Streamlit dashboard
- [ ] Phase 5: polish, insights, deployment
