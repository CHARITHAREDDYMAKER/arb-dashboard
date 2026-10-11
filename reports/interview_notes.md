# Interview notes

All figures are as of 9 Oct 2026, with default desk inputs (brokerage Rs 10 per
lakh, funding at T-bill + 1%, handling Rs 10 per quintal, 18% GST on storage
and assaying). If the data has been refreshed, read the header again before
quoting today's numbers. Figures marked † depend on the handling or GST assumption.

## Numbers to remember

1. **1.4% a month:** the average upward slope of both futures curves, roughly 17% a year, against a T-bill near 5.3%.
2. **-2.0% to +0.5%:** jeera's average basis (spot minus futures) at 46 to 60 days out, and at 6 to 10 days out. Futures go from 2% above spot to below it; the premium is gone before expiry.
3. **64%:** share of days guar seed was a tradeable reverse arb †. Cash-and-carry was tradeable on only 14% †.
4. **0.07%:** average gap between final settlement and the 3-day average spot, across 23 expired contracts.
5. **Rs 2.9 crore and Rs 10.7 crore:** median daily capacity in jeera and guar seed at 10% of volume.

Best dates (contracts with 15 or more days to expiry) †:

| Side | Date | Days to expiry | Net Rs per quintal | Annualised | Against the T-bill |
|---|---|---|---|---|---|
| Jeera cash-and-carry | 28 Nov 2025 | 21 | 909.54 | 81.16% | +75.82 points |
| Jeera reverse arb | 4 Jun 2026 | 15 | 671.11 | 83.68% (77.20% excluding avoided funding) | +71.72 points |
| Guar seed cash-and-carry | 2 Sep 2026 | 16 | 170.64 | 65.21% | +59.96 points |
| Guar seed reverse arb | 4 Mar 2026 | 16 | 108.20 | 46.33% (40.02% excluding avoided funding) | +34.71 points |

The one to say out loud: **jeera, 28 Nov 2025: Rs 910 a quintal, 4.67% in 21 days.** Then add that the median tradeable day earned about 16% annualised, so the best day is the exception.

## Three-minute demo script

Tab order in the app: Overview, Signal Finder, Cash vs Futures, Arb Economics, Term Structure, Convergence & Entry Timing, Rollover & Capacity, Method & Data. The demo uses five of them. Lead with the question, then the finding, then the evidence.

**0:00 Overview (40 seconds)**
- Say: "A cash-futures desk buys the commodity, stores it, sells futures and delivers. The question is whether the gap between futures and spot pays for the carry. I measured that for jeera and guar seed on NCDEX over a year, with real warehouse and exchange costs."
- Point to the three cards: "Three things came out. The carry is earned early in a contract's life. Guar seed is mostly a reverse-arb market. And the model is tight, so the real limit is size."

**0:40 Signal Finder (60 seconds)**
- Question: "What does each trade actually earn, line by line?"
- In the date box type **28 Nov 2025** (jeera). Say: "This was the best cash-and-carry day of the year. Futures were Rs 1,077 above spot with 21 days to run. Storage, assaying, handling and fees take Rs 90, funding takes Rs 77, and Rs 910 a quintal is left: 4.67% in 21 days."
- Point to the timeline: "Each marker is a day. Teal is cash-and-carry, orange is reverse arb. They come in clusters, not single days."
- Switch the commodity to **Guar seed** and pick **4 Mar 2026**. Say: "This is the mirror image, the best reverse-arb day. Futures were Rs 86 below spot. A stock holder sells spot, buys futures and gains Rs 108 a quintal with 16 days to run. It needs stock already held, which is why this gap persists."
- Switch back to jeera.

**1:40 Cash vs Futures (25 seconds)**
- Question: "How far do futures usually sit from spot?"
- Finding: "I define basis as spot minus futures, so it is negative when futures are above spot. Jeera futures closed above spot on 61% of days. The basis ranged from -6.9% to +3.7% and averaged -0.45%. Today's, -0.83%, is ordinary."

**2:05 Convergence & Entry Timing (40 seconds)**
- Question: "Does a contract keep its premium as delivery approaches?"
- Finding: "No. Jeera futures average 2% over spot two months out and trade below spot in the last ten days."
- Evidence: the bucket chart, then the entry-timing chart. "Assaying and handling are paid once, about Rs 69 a quintal †. Entered 46 to 60 days out the trade averaged +Rs 119 †; inside ten days it averaged -Rs 215 †."

**2:45 Method & Data (15 seconds)**
- "Spot matched the exchange's page exactly on the day I checked. Contracts settled within 0.07% of where the model says. Two cost inputs are assumptions and they are marked."

If asked to change something live: raise handling to Rs 25, or switch GST off, and show the header and the tradeable-day counts move. If asked about size, open Rollover & Capacity: about Rs 3 crore a day in jeera and Rs 11 crore in guar seed.

## Likely questions

**Why does reverse arb persist in guar seed?**
Closing it means selling physical guar seed today and buying futures. Nobody can borrow physical stock to sell short, so only someone who already holds stock can do the trade. They do it only as far as they are willing to part with inventory, and they give up the convenience of holding it. So a futures discount is a payment to stockists, not free money. Futures sat below fair value on 64% of days †.

**Why is the return quoted before funding?**
To compare it with the T-bill without counting interest twice. The dashboard shows two measures. "Carry return" is the annualised return on the cash invested after physical and transaction costs; compare that with the T-bill. "Net profit" then deducts funding at T-bill plus the spread. Net profit is positive exactly when the carry return beats the funding rate; a unit test checks this.

**How do contracts settle? What is the 3-day average?**
The final settlement price matches the average spot of the expiry day and the two trading days before it. I inferred this from the data: across 23 expired contracts the gap to that average is 0.07% on average, against 0.61% for same-day spot. I have not read it in the contract specification, and I would confirm it there before trading. It matters because a position held to expiry converges to the average, not the last print.

**Why is stamp duty zero on cash-and-carry?**
Stamp duty on commodity futures is 0.002% and is charged to the buyer. In cash-and-carry the futures are sold and then delivered against, so there is no futures purchase. It does apply to reverse arb, where futures are bought. Commodities transaction tax is nil on both: jeera and guar seed are exempt agricultural commodities.

**What is not modelled, and how would it change the result?**
- Repository and repository-participant charges: a small extra cost; lowers cash-and-carry returns slightly.
- Minimum holding period on storage: raises the cost of very short trades, which are already uneconomic.
- Margin funding on the short futures: capital tied up for the holding period; lowers return on total capital.
- Mandi fees, commission and transport on the spot purchase: probably the largest omission; could remove many marginal cash-and-carry days.
- Quality: a lot can fail assay or be delivered at a discount. This is a tail risk, not an average cost.
- The polled spot price is a reference price. A desk's actual purchase price will differ from it.
Every omission hurts cash-and-carry and most leave reverse arb unchanged, so the true picture is somewhat more reverse-heavy than shown.

**How much can be traded?**
At 10% of 20-day average volume: a median of Rs 2.9 crore a day in jeera (about 44 lots of 3 MT) and Rs 10.7 crore in guar seed (about 367 lots of 5 MT). Open interest moves early: five trading days before expiry the next month already holds 72% of jeera's and 87% of guar seed's combined open interest. A position has to be built over days and in the right contract.

**How would this run live?**
Run `python -m src.pipeline --update` after the bhavcopy is published each evening, then rebuild the metrics. Add an alert when the tradeable signal changes or when net profit per quintal crosses a threshold. For intraday use the daily close would be replaced by live quotes and the polled spot by the desk's own purchase price. The cost model is a pure function, so it takes any prices.

**How would it extend to other commodities?**
Add an entry in `config/commodities.toml` (symbol, lot size, basis centre) and the storage and assaying rates in `config/carry_costs.toml`. The bhavcopy already contains every NCDEX contract, so no new download is needed. Each new commodity needs its own checks: is the underlying price the delivery-centre spot, does it converge at expiry, and is it CTT-exempt.

**Why is the best date 81% annualised when the median is about 16%?**
Because it is the best of 106 tradeable days, and annualising stretches it. On 28 Nov 2025 the trade earned 4.67% in 21 days; multiplying by 365/21 gives 81%. That assumes the same gap could be found again seventeen times in a row, which it cannot. The tenth-best day earned about 36% and the median tradeable day about 16%, still roughly ten points over the T-bill. I would quote the median for what the strategy earns and the best day only as an example of the calculation.

**Why rank only contracts with 15 or more days to expiry?**
Annualising a short trade exaggerates it. Guar seed's best reverse day by rupees was 13 Jan 2026: Rs 233 a quintal with 7 days left, which annualises to 212%. A small gain multiplied by 52 is not a return anyone can compound. Short trades are also the ones where the one-off assaying and handling costs hurt most and where liquidity has already moved to the next contract. They are shown in a separate table, labelled as exaggerated, and the header leads with rupees for them.

**How does the reverse-arb comparison with the T-bill work?**
Reverse-arb benefit has three parts: the basis (spot minus futures), the storage saved, and the interest saved on the cash from selling the stock. That interest is itself earned at about the T-bill rate (T-bill plus the spread). Comparing the full return with the T-bill would count it twice. So the dashboard compares the return excluding avoided funding. On 4 Jun 2026 jeera reverse arb earned 83.68% annualised in full; excluding the Rs 52 of interest saved it was 77.20%, which is 71.72 points over a 5.48% T-bill. Across all tradeable reverse days the median is about 26% in full and 19% excluding avoided funding. This mirrors cash-and-carry, whose return is quoted before funding for the same reason.

**What does the arrival season have to do with the best dates?**
The dashboard counts how many of the top dates fell in each crop's typical arrival season. Those months are general market knowledge, not exchange data, and the count is an observation, not a cause. One year is one crop cycle.

**How do you define basis?**
Spot minus futures, the textbook convention. A negative basis means futures are above spot, which is the cash-and-carry side; a positive basis means futures are below spot, the reverse-arb side. Some desks quote it the other way round (futures minus spot), so I say the definition before quoting a number. In the dashboard the futures-minus-spot figure is called the futures premium; it is the same number with its sign flipped.

**Why did you not use same-day spot for convergence?**
Because it understates how well contracts converge. Against same-day spot the average gap is 0.61%; against the 3-day average it is 0.07%.

**Why does the active contract roll five days before expiry?**
In expiry week the basis is collapsing, liquidity has moved to the next month, and annualising a return over two or three days gives meaningless numbers. The threshold is an input.

**How do you know the spot price is right?**
Three checks. It matched the NCDEX spot page exactly on 9 Oct 2026 for both commodities (one date, checked by hand). Every contract of a commodity carries the same value on every day. And contracts settle to it within 0.07%.

## Honest caveats

- **Handling cost is an assumption:** Rs 10 per quintal. NCCL does not publish loading and unloading charges. At Rs 25, tradeable cash-and-carry days in jeera fall from about 43% to 40% †.
- **GST on warehouse charges is an assumption:** 18% is applied as the conservative case. Storage of agricultural produce may be exempt. Switching it off moves jeera's tradeable cash-and-carry days from 43.1% to 44.3%, so the conclusion does not depend on it.
- **The SEBI turnover fee is from the 2019 notification** (Rs 1 per crore for agricultural commodity derivatives). The broker regulations were re-issued in 2026; secondary sources say the rate is unchanged, but I have not read the current schedule. It is a very small number either way.
- **The 3-day settlement rule is inferred**, not read from the contract specification.
- **Spot was cross-checked on one date only.**
- **One year of data is one crop cycle.** The seasonality chart shows a pattern to test, not a rule.
- **Arrival-season months are the typical arrival season (general market knowledge, not from exchange data).** They are reported next to the best dates as an observation, never as a cause.
- **Best dates are the top of the distribution.** Quote the median (about 16% annualised for cash-and-carry) for what the strategy earns.
- **Guar seed assaying** is charged per 10 MT sample lot. A single 5 MT lot pays the full charge, double the Rs 15 per quintal used here.
- **The last five T-bill values are RBI auction yields**, used until FBIL publishes; they sit about 0.08 points above the FBIL series.
- **Short-dated figures:** on 9 Oct both active contracts had 10 days or fewer to run, so the header leads with rupees per quintal. Do not quote the annualised figures for those days.

## If asked "what would you do next?"

1. Get real handling and mandi costs from a warehouse operator and a commission agent.
2. Read the contract specifications to confirm the settlement rule and the delivery premiums and discounts.
3. Add the calendar-spread trade (hold stock, sell the next month) as a third strategy; the data says that is where the carry is.
4. Extend history to three or four years to test the seasonal pattern.
