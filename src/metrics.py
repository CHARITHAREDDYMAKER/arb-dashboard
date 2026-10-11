"""Metrics for the cash-futures arbitrage monitor.

Pure functions: DataFrames and inputs in, DataFrames out. Nothing here reads
files or the network, so the dashboard can call the same functions with the
user's own cost inputs and get fresh numbers instantly.

Conventions
  * All money amounts are Rs per quintal (the unit futures and spot are quoted in).
  * Percentages are in percent (5.5 means 5.5%), annualised on a 365-day year.
  * Only `settlement_price` is used; OHLC is 0 on days a contract did not trade.
  * Basis = spot - futures (the textbook convention). Negative basis means futures
    are above spot (the cash-and-carry side); positive means futures are below spot
    (the reverse-arb side). The cash-and-carry gross gain, futures - spot, is called
    the futures premium and is simply the basis with its sign flipped.
  * Most functions take ONE commodity's rows, because cost inputs differ by commodity.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

DAYS_IN_YEAR = 365
AVG_DAYS_IN_MONTH = 365 / 12
QUINTALS_PER_MT = 10
RS_PER_LAKH = 1e5
RS_PER_CRORE = 1e7

SIGNAL_CASH_AND_CARRY = "CASH_AND_CARRY"
SIGNAL_REVERSE = "REVERSE"
SIGNAL_NONE = "NONE"

CONVERGENCE_BUCKET_EDGES = [-1, 5, 10, 20, 30, 45, 60]
CONVERGENCE_BUCKET_LABELS = ["0-5", "6-10", "11-20", "21-30", "31-45", "46-60"]
ROLL_WINDOW_DAYS = 5  # trading days before expiry examined in the rollover table
SHORT_DATED_DAYS = 15  # below this, annualising a return exaggerates it, so rupees and the holding-period return lead


@dataclass(frozen=True)
class CostInputs:
    """Everything the cost model needs for one commodity. All are dashboard inputs."""

    storage_rs_per_quintal_per_day: float
    assaying_rs_per_quintal: float
    handling_rs_per_quintal: float
    brokerage_rs_per_lakh: float
    funding_spread_pct: float  # % p.a. over the T-bill yield
    exchange_fee_rs_per_lakh: float
    sebi_fee_rs_per_crore: float
    stamp_duty_pct: float  # on the futures BUY side only
    gst_pct: float  # on brokerage and the exchange fee
    gst_on_storage_and_assaying: bool
    ctt_pct: float = 0.0  # jeera and guar seed are exempt agricultural commodities

    @property
    def warehouse_gst_multiplier(self) -> float:
        return 1 + self.gst_pct / 100 if self.gst_on_storage_and_assaying else 1.0


@dataclass(frozen=True)
class Rules:
    roll_dte_threshold: int = 5
    min_oi: float = 1
    signal_threshold_pct: float = 0.25
    capacity_share_of_volume: float = 0.10
    capacity_window_days: int = 20


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def annualise(return_pct, days):
    """Scale a return earned over `days` to a 365-day year. Undefined for 0 days."""
    days = np.where(np.asarray(days) > 0, days, np.nan)
    return return_pct * DAYS_IN_YEAR / days


def add_three_day_spot(spot: pd.DataFrame) -> pd.DataFrame:
    """Add `spot_3d_avg`: mean spot of this trading day and the two before it.

    The exchange's final settlement price matches this average almost exactly
    (see reports/phase2_data_quality.md). The 3-day rule is INFERRED FROM THE
    DATA, not read from the contract specification.
    """
    spot = spot.sort_values(["commodity", "date"]).copy()
    spot["spot_3d_avg"] = spot.groupby("commodity")["spot"].transform(lambda prices: prices.rolling(3).mean())
    return spot


def mark_active_contract(futures: pd.DataFrame, roll_dte_threshold: int) -> pd.DataFrame:
    """Add `is_active`: the one contract per day that the trade is priced on.

    Active = the near month (contract_rank 1), until it has fewer than
    `roll_dte_threshold` calendar days left; then the next month (rank 2).
    In expiry week the basis is collapsing and annualising over a few days
    produces meaningless numbers, so we roll early.

    The rule works on ranks, not calendar months, so jeera's missing February
    contract needs no special case: after January expires the March contract
    simply becomes rank 1 with about 60 days to run.
    """
    near = futures.loc[futures["contract_rank"] == 1, ["date", "commodity", "days_to_expiry"]]
    futures = futures.merge(near.rename(columns={"days_to_expiry": "near_dte"}), on=["date", "commodity"], how="left")
    active_rank = np.where(futures["near_dte"] < roll_dte_threshold, 2, 1)
    futures["is_active"] = (futures["contract_rank"] == active_rank).fillna(False).astype(bool)
    return futures.drop(columns="near_dte")


def futures_leg_costs(futures_price, costs: CostInputs, buys_futures: bool) -> dict:
    """Costs of trading one futures leg, Rs per quintal.

    Fees are quoted per lakh or per crore of turnover; turnover per quintal is
    the futures price. Stamp duty is levied on the buyer only, so it applies
    to reverse arb (buy futures) and not to cash-and-carry (sell and deliver).
    """
    exchange_fee = futures_price * costs.exchange_fee_rs_per_lakh / RS_PER_LAKH
    brokerage = futures_price * costs.brokerage_rs_per_lakh / RS_PER_LAKH
    return {
        "exchange_fee": exchange_fee,
        "sebi_fee": futures_price * costs.sebi_fee_rs_per_crore / RS_PER_CRORE,
        "stamp_duty": futures_price * costs.stamp_duty_pct / 100 * (1 if buys_futures else 0),
        "brokerage": brokerage,
        "gst_on_fees": (exchange_fee + brokerage) * costs.gst_pct / 100,
        "ctt": futures_price * costs.ctt_pct / 100 * (0 if buys_futures else 1),
    }


def funding_cost(spot, tbill_pct, days, costs: CostInputs):
    """Interest on the cash tied up in the goods, at T-bill + spread."""
    return spot * (tbill_pct + costs.funding_spread_pct) / 100 * days / DAYS_IN_YEAR


def storage_cost(days, costs: CostInputs):
    """Warehouse rent for `days`, including GST if the switch is on. Insurance is in the rent."""
    return costs.storage_rs_per_quintal_per_day * days * costs.warehouse_gst_multiplier


# --------------------------------------------------------------------------- #
# 2. Cash-and-carry and 3. reverse arb
# --------------------------------------------------------------------------- #


def cash_and_carry(spot, futures_price, days, tbill_pct, costs: CostInputs) -> dict:
    """Economics of: buy spot today, store, sell futures, deliver at expiry.

    Returns every cost line plus:
      cc_net_profit         profit after ALL costs including funding. Positive
                            means the trade beats borrowing at T-bill + spread.
      cc_carry_return_pct   annualised return on the cash invested, after
                            physical and transaction costs but BEFORE funding.
                            This is the number to compare with the T-bill yield.
      cc_excess_over_tbill  that return minus the T-bill yield.
      break_even_storage    storage rate (Rs/quintal/day, before GST) at which
                            cc_net_profit is exactly zero.

    Funding is kept out of the return and inside the profit so that it is not
    counted twice when the return is compared with the T-bill.
    """
    gross = futures_price - spot
    funding = funding_cost(spot, tbill_pct, days, costs)
    storage = storage_cost(days, costs)
    assaying = costs.assaying_rs_per_quintal * costs.warehouse_gst_multiplier
    handling = costs.handling_rs_per_quintal
    leg = futures_leg_costs(futures_price, costs, buys_futures=False)
    transaction = sum(leg.values())

    non_funding_costs = storage + assaying + handling + transaction
    net_profit = gross - funding - non_funding_costs
    carry_return = annualise((gross - non_funding_costs) / spot * 100, days)

    paid_days = np.where(np.asarray(days) > 0, days, np.nan) * costs.warehouse_gst_multiplier
    # GST shown on its own line in the cost waterfall: on fees, and on warehouse charges if switched on.
    warehouse_gst = (storage + assaying) * (1 - 1 / costs.warehouse_gst_multiplier)
    return {
        "cc_gross": gross,
        "cc_funding": funding,
        "cc_storage": storage,
        "cc_assaying": assaying,
        "cc_handling": handling,
        "cc_transaction": transaction,
        "cc_fees_ex_gst": transaction - leg["gst_on_fees"],
        "cc_gst": leg["gst_on_fees"] + warehouse_gst,
        "cc_total_costs": funding + non_funding_costs,
        "cc_carry_profit": gross - non_funding_costs,  # before funding
        "cc_holding_return_pct": (gross - non_funding_costs) / spot * 100,  # before funding, not annualised
        "cc_net_profit": net_profit,
        "cc_net_profit_pct": net_profit / spot * 100,
        "cc_return_after_funding_pct": annualise(net_profit / spot * 100, days),  # annualised, AFTER funding
        "cc_carry_return_pct": carry_return,
        "cc_excess_over_tbill": carry_return - tbill_pct,
        "break_even_storage": (net_profit + storage) / paid_days,
    }


def reverse_arb(spot, futures_price, days, tbill_pct, costs: CostInputs) -> dict:
    """Economics of: sell stock you already hold at spot, buy futures, take delivery at expiry.

    The seller ends up with the same goods at expiry, having earned
      spot - futures            the price gap
      + avoided funding         cash is free for `days`
      + avoided storage         no warehouse rent for `days`
      - transaction costs       on the futures purchase (includes stamp duty)

    This trade needs EXISTING INVENTORY: nobody can short physical jeera or guar
    seed. That is why futures can stay below fair value for long stretches. Only
    stockists can close the gap, and they do so only as far as they are willing
    to part with stock.
    """
    leg = futures_leg_costs(futures_price, costs, buys_futures=True)
    transaction = sum(leg.values())
    avoided_funding = funding_cost(spot, tbill_pct, days, costs)
    avoided_carry = avoided_funding + storage_cost(days, costs)
    benefit = spot - futures_price + avoided_carry - transaction
    # The benefit includes interest saved on the cash (avoided funding), which is itself
    # earned at T-bill + spread. Comparing the full return with the T-bill would count that
    # interest twice, so the T-bill comparison uses the return EXCLUDING avoided funding.
    # This mirrors cash-and-carry, whose return is quoted before funding.
    benefit_ex_funding = benefit - avoided_funding
    return_ex_funding = annualise(benefit_ex_funding / spot * 100, days)
    return {
        "rev_price_gap": spot - futures_price,
        "rev_avoided_funding": avoided_funding,
        "rev_avoided_carry": avoided_carry,
        "rev_transaction": transaction,
        "rev_benefit": benefit,
        "rev_benefit_pct": benefit / spot * 100,
        "rev_return_pct": annualise(benefit / spot * 100, days),
        "rev_benefit_ex_funding": benefit_ex_funding,
        "rev_return_ex_funding_pct": return_ex_funding,
        "rev_excess_over_tbill": return_ex_funding - tbill_pct,
    }


# --------------------------------------------------------------------------- #
# 4. Fair value and signal
# --------------------------------------------------------------------------- #


def fair_value(spot, days, tbill_pct, costs: CostInputs):
    """Cost-of-carry fair price of the future: spot plus funding and storage to expiry."""
    return spot + funding_cost(spot, tbill_pct, days, costs) + storage_cost(days, costs)


def implied_carry(spot, futures_price, days):
    """The annual rate the futures price implies for carrying the goods to expiry.

    implied carry = ln(futures / spot) x 365 / days x 100. Compare it with the
    cost of carry (funding plus storage): above it, futures are rich; below it, cheap.
    """
    days = np.where(np.asarray(days) > 0, days, np.nan)
    return np.log(np.asarray(futures_price, dtype=float) / np.asarray(spot, dtype=float)) * DAYS_IN_YEAR / days * 100


def cost_of_carry(spot, days, tbill_pct, costs: CostInputs):
    """Funding plus storage to expiry as an annual % of spot: the rate fair value is built from."""
    carry = funding_cost(spot, tbill_pct, days, costs) + storage_cost(days, costs)
    return annualise(carry / spot * 100, days)


def classify_signal(mispricing_pct, threshold_pct: float):
    """CASH_AND_CARRY if futures are rich to fair value by more than the threshold,
    REVERSE if cheap by more than the threshold, otherwise NONE."""
    mispricing_pct = np.asarray(mispricing_pct, dtype=float)
    return np.select(
        [mispricing_pct > threshold_pct, mispricing_pct < -threshold_pct],
        [SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE],
        default=SIGNAL_NONE,
    )


def classify_tradeable(signal, cash_and_carry_profit, reverse_benefit):
    """Keep a signal only if the trade makes money after ALL costs.

    The raw signal compares futures with fair value, which leaves out the one-off
    assaying and handling costs. A cash-and-carry signal is TRADEABLE only if its
    net profit is positive; a reverse signal only if its net benefit is positive.
    """
    signal = np.asarray(signal)
    carry_pays = (signal == SIGNAL_CASH_AND_CARRY) & (np.asarray(cash_and_carry_profit, dtype=float) > 0)
    reverse_pays = (signal == SIGNAL_REVERSE) & (np.asarray(reverse_benefit, dtype=float) > 0)
    return np.where(carry_pays | reverse_pays, signal, SIGNAL_NONE)


# --------------------------------------------------------------------------- #
# 1. Basis table and the daily economics of the active contract
# --------------------------------------------------------------------------- #


def basis_table(futures: pd.DataFrame, spot: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Basis of every contract on every day, with the active contract marked.

    basis = spot - settlement price. Negative = futures above spot.
    `basis_vs_3d_avg_pct` uses the 3-day average spot, the level the contract
    actually settles to at expiry.
    """
    table = mark_active_contract(futures, rules.roll_dte_threshold)
    table = table.merge(add_three_day_spot(spot)[["date", "commodity", "spot_3d_avg"]], on=["date", "commodity"], how="left")
    table["basis"] = table["spot"] - table["settlement_price"]
    table["basis_pct"] = table["basis"] / table["spot"] * 100
    table["basis_ann_pct"] = annualise(table["basis_pct"], table["days_to_expiry"])
    table["basis_vs_3d_avg_pct"] = (1 - table["settlement_price"] / table["spot_3d_avg"]) * 100
    # Futures relative to spot, for the term-structure curve ("% above spot"): the basis % with its sign flipped.
    table["futures_premium_pct"] = (table["settlement_price"] / table["spot"] - 1) * 100
    table["term"] = np.select([table["contract_rank"] == 1, table["contract_rank"] == 2], ["near", "next"], default="far")
    return table


def daily_economics(basis: pd.DataFrame, tbill: pd.DataFrame, costs: CostInputs, rules: Rules) -> pd.DataFrame:
    """One row per day for ONE commodity: the active contract's cash-and-carry and
    reverse-arb economics, fair value and signal. Recomputed whenever inputs change."""
    active = basis.loc[basis["is_active"]].merge(
        tbill.rename(columns={"yield_pct": "tbill_pct", "source": "tbill_source"}), on="date", how="left"
    )
    spot, price, days, rate = active["spot"], active["settlement_price"], active["days_to_expiry"], active["tbill_pct"]

    active = active.assign(**cash_and_carry(spot, price, days, rate, costs), **reverse_arb(spot, price, days, rate, costs))
    active["fair_value"] = fair_value(spot, days, rate, costs)
    active["implied_carry_pct"] = implied_carry(spot, price, days)
    active["cost_of_carry_pct"] = cost_of_carry(spot, days, rate, costs)
    active["mispricing"] = price - active["fair_value"]
    active["mispricing_pct"] = active["mispricing"] / spot * 100
    active["signal"] = classify_signal(active["mispricing_pct"], rules.signal_threshold_pct)
    active["tradeable_signal"] = classify_tradeable(active["signal"], active["cc_net_profit"], active["rev_benefit"])
    active["tbill_is_rbi_fill"] = active["tbill_source"] == "rbi_fill"
    active["actual_storage"] = costs.storage_rs_per_quintal_per_day
    return active.sort_values("date").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 5. Opportunity log
# --------------------------------------------------------------------------- #


def expiry_settlements(basis: pd.DataFrame) -> pd.DataFrame:
    """Final settlement of each expired contract against the 3-day average spot.

    `convergence_error_pct` is how far the contract settled from the level the
    3-day rule predicts; it is the residual risk of holding to expiry.
    """
    last_date = basis["date"].max()
    final = basis.loc[(basis["date"] == basis["expiry"]) & (basis["expiry"] <= last_date)]
    final = final.assign(
        convergence_error_pct=(final["settlement_price"] / final["spot_3d_avg"] - 1) * 100,
        same_day_gap_pct=(final["settlement_price"] / final["spot"] - 1) * 100,
    )
    columns = ["commodity", "contract_symbol", "expiry", "settlement_price", "spot", "spot_3d_avg", "same_day_gap_pct", "convergence_error_pct"]
    return final[columns].rename(columns={"settlement_price": "final_settlement"}).sort_values(["commodity", "expiry"])


def opportunity_log(economics: pd.DataFrame, settlements: pd.DataFrame) -> pd.DataFrame:
    """Every day with a TRADEABLE signal, with the return locked in by holding to expiry."""
    log = economics.loc[economics["tradeable_signal"] != SIGNAL_NONE].copy()
    log["signal"] = log["tradeable_signal"]
    is_carry = log["signal"] == SIGNAL_CASH_AND_CARRY
    log["locked_profit"] = np.where(is_carry, log["cc_net_profit"], log["rev_benefit"])
    log["locked_return_ann_pct"] = np.where(is_carry, log["cc_carry_return_pct"], log["rev_return_pct"])
    log = log.merge(settlements[["contract_symbol", "convergence_error_pct"]], on="contract_symbol", how="left")
    log["expired"] = log["convergence_error_pct"].notna()
    columns = [
        "date", "commodity", "signal", "contract_symbol", "expiry", "days_to_expiry", "spot", "settlement_price",
        "mispricing_pct", "locked_profit", "locked_return_ann_pct", "expired", "convergence_error_pct", "tbill_is_rbi_fill",
    ]  # fmt: skip
    return log[columns].rename(columns={"date": "entry_date"})


def opportunity_summary(economics: pd.DataFrame, log: pd.DataFrame) -> pd.DataFrame:
    """Tradeable signal days and locked returns by side."""
    total_days = len(economics)
    summary = (
        log.groupby(["commodity", "signal"])
        .agg(
            signal_days=("entry_date", "size"),
            avg_locked_return_ann_pct=("locked_return_ann_pct", "mean"),
            median_locked_return_ann_pct=("locked_return_ann_pct", "median"),
            best_locked_return_ann_pct=("locked_return_ann_pct", "max"),
            avg_locked_profit=("locked_profit", "mean"),
        )
        .reset_index()
    )
    summary["share_of_days_pct"] = summary["signal_days"] / total_days * 100
    return summary


# --------------------------------------------------------------------------- #
# 6. Convergence
# --------------------------------------------------------------------------- #


def convergence_paths(basis: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Basis % of each contract over its last 60 days, on days it had open interest."""
    paths = basis.loc[(basis["days_to_expiry"] <= 60) & (basis["open_interest"] >= rules.min_oi)].copy()
    paths["dte_bucket"] = pd.cut(paths["days_to_expiry"], CONVERGENCE_BUCKET_EDGES, labels=CONVERGENCE_BUCKET_LABELS).astype(str)
    columns = ["date", "commodity", "contract_symbol", "expiry", "days_to_expiry", "dte_bucket", "basis_pct", "basis_vs_3d_avg_pct"]
    return paths[columns]


def convergence_buckets(paths: pd.DataFrame) -> pd.DataFrame:
    """Average basis % by days-to-expiry bucket, across contracts.

    The mean shows direction: negative while futures sit above spot, positive
    once they fall below it. The mean absolute value shows how tight the basis
    is, whatever its sign.
    """
    buckets = (
        paths.groupby(["commodity", "dte_bucket"])
        .agg(
            observations=("basis_pct", "size"),
            contracts=("contract_symbol", "nunique"),
            mean_basis_pct=("basis_pct", "mean"),
            mean_abs_basis_pct=("basis_pct", lambda b: b.abs().mean()),
            mean_abs_basis_vs_3d_avg_pct=("basis_vs_3d_avg_pct", lambda b: b.abs().mean()),
        )
        .reset_index()
    )
    buckets["dte_bucket"] = pd.Categorical(buckets["dte_bucket"], CONVERGENCE_BUCKET_LABELS, ordered=True)
    return buckets.sort_values(["commodity", "dte_bucket"]).astype({"dte_bucket": str}).reset_index(drop=True)


def entry_timing(basis: pd.DataFrame, tbill: pd.DataFrame, costs: CostInputs, rules: Rules) -> pd.DataFrame:
    """Cash-and-carry economics of entering ANY held contract (ONE commodity) on any day.

    Unlike `daily_economics`, this is not limited to the active contract: it asks
    what a trade entered N days before expiry would have locked in. Assaying and
    handling are paid once whatever the holding period, so they weigh most on
    short trades.
    """
    held = basis.loc[(basis["open_interest"] >= rules.min_oi) & basis["days_to_expiry"].between(1, 60)]
    held = held.merge(tbill.rename(columns={"yield_pct": "tbill_pct"})[["date", "tbill_pct"]], on="date", how="left")
    result = cash_and_carry(held["spot"], held["settlement_price"], held["days_to_expiry"], held["tbill_pct"], costs)
    held = held.assign(
        cc_gross=result["cc_gross"],
        cc_net_profit=result["cc_net_profit"],
        cc_fixed_costs=result["cc_assaying"] + result["cc_handling"],
        cc_carry_return_pct=result["cc_carry_return_pct"],
    )
    held["dte_bucket"] = pd.cut(held["days_to_expiry"], CONVERGENCE_BUCKET_EDGES, labels=CONVERGENCE_BUCKET_LABELS).astype(str)
    columns = [
        "date", "commodity", "contract_symbol", "days_to_expiry", "dte_bucket",
        "cc_gross", "cc_fixed_costs", "cc_net_profit", "cc_carry_return_pct",
    ]  # fmt: skip
    return held[columns]


def entry_timing_buckets(timing: pd.DataFrame) -> pd.DataFrame:
    """Average outcome of a cash-and-carry by how many days before expiry it was entered."""
    buckets = (
        timing.groupby("dte_bucket")
        .agg(
            observations=("cc_net_profit", "size"),
            avg_gross=("cc_gross", "mean"),
            avg_net_profit=("cc_net_profit", "mean"),
            median_net_profit=("cc_net_profit", "median"),
            profitable_pct=("cc_net_profit", lambda profit: (profit > 0).mean() * 100),
            fixed_costs=("cc_fixed_costs", "mean"),
        )
        .reindex(CONVERGENCE_BUCKET_LABELS)
        .dropna(subset=["observations"])
    )
    return buckets.rename_axis("dte_bucket").reset_index()


# --------------------------------------------------------------------------- #
# 7. Term structure
# --------------------------------------------------------------------------- #


def term_structure(basis: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """The futures curve on each date: contracts with open interest only.

    Contracts nobody holds carry a theoretical settlement price, so they are
    left out. `curve_position` re-numbers the surviving contracts 1, 2, 3...
    """
    curve = basis.loc[basis["open_interest"] >= rules.min_oi].copy()
    curve["curve_position"] = curve.groupby(["date", "commodity"])["expiry"].rank(method="first").astype(int)
    columns = [
        "date", "commodity", "contract_symbol", "expiry", "days_to_expiry", "curve_position", "term",
        "settlement_price", "spot", "futures_premium_pct", "open_interest",
    ]  # fmt: skip
    return curve[columns].rename(columns={"futures_premium_pct": "pct_vs_spot"})


def term_structure_daily(curve: pd.DataFrame) -> pd.DataFrame:
    """Shape of the curve each day: slope from the nearest to the farthest held contract.

    slope_pct_per_month = (far / near - 1) * 100 / months between their expiries.
    Positive = contango (later months dearer), negative = backwardation.
    Days with only one held contract have no slope.
    """
    ordered = curve.sort_values("expiry")
    daily = ordered.groupby(["date", "commodity"]).agg(
        contracts=("expiry", "size"),
        near_price=("settlement_price", "first"),
        far_price=("settlement_price", "last"),
        near_expiry=("expiry", "first"),
        far_expiry=("expiry", "last"),
        spot=("spot", "first"),
    )
    months_apart = (daily["far_expiry"] - daily["near_expiry"]).dt.days / AVG_DAYS_IN_MONTH
    daily["slope_pct_per_month"] = (daily["far_price"] / daily["near_price"] - 1) * 100 / months_apart.where(months_apart > 0)
    daily["shape"] = np.select(
        [daily["slope_pct_per_month"] > 0, daily["slope_pct_per_month"] < 0], ["contango", "backwardation"], default="flat"
    )
    daily.loc[daily["slope_pct_per_month"].isna(), "shape"] = "single contract"
    return daily.reset_index()


def term_structure_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    """Calendar-month averages of the curve slope, to show how it changes through the year."""
    with_slope = daily.dropna(subset=["slope_pct_per_month"]).copy()
    with_slope["month"] = with_slope["date"].dt.to_period("M").dt.to_timestamp()
    return (
        with_slope.groupby(["commodity", "month"])
        .agg(
            days=("date", "size"),
            avg_slope_pct_per_month=("slope_pct_per_month", "mean"),
            contango_days_pct=("shape", lambda shape: (shape == "contango").mean() * 100),
            avg_spot=("spot", "mean"),
        )
        .reset_index()
    )


# --------------------------------------------------------------------------- #
# 8. Rollover
# --------------------------------------------------------------------------- #


def rollover(basis: pd.DataFrame) -> pd.DataFrame:
    """The last trading days of each expired contract, next to the following contract.

    calendar_spread = next - near (Rs/quintal): what it costs a long to roll, or
    what a cash-and-carry earns by re-selling the next month.
    roll_pct = next-month share of the two contracts' combined open interest;
    it climbs towards 100 as positions move out of the expiring month.
    """
    near = basis.loc[basis["contract_rank"] == 1].copy()
    near = near.loc[near["expiry"] <= basis["date"].max()]
    near["trading_days_to_expiry"] = near.groupby("contract_symbol")["date"].rank(ascending=False).astype(int) - 1
    near = near.loc[near["trading_days_to_expiry"] < ROLL_WINDOW_DAYS]

    following = basis.loc[basis["contract_rank"] == 2, ["date", "commodity", "contract_symbol", "expiry", "settlement_price", "open_interest", "volume"]]
    pair = near.merge(following, on=["date", "commodity"], suffixes=("_near", "_next"))

    days_between = (pair["expiry_next"] - pair["expiry_near"]).dt.days
    pair["calendar_spread"] = pair["settlement_price_next"] - pair["settlement_price_near"]
    pair["calendar_spread_ann_pct"] = annualise(pair["calendar_spread"] / pair["settlement_price_near"] * 100, days_between)
    combined_oi = pair["open_interest_near"] + pair["open_interest_next"]
    combined_volume = pair["volume_near"] + pair["volume_next"]
    pair["roll_pct"] = pair["open_interest_next"] / combined_oi.where(combined_oi > 0) * 100
    pair["next_volume_share_pct"] = pair["volume_next"] / combined_volume.where(combined_volume > 0) * 100
    columns = [
        "date", "commodity", "contract_symbol_near", "contract_symbol_next", "expiry_near", "trading_days_to_expiry",
        "calendar_spread", "calendar_spread_ann_pct", "roll_pct", "next_volume_share_pct",
        "open_interest_near", "open_interest_next", "volume_near", "volume_next",
    ]  # fmt: skip
    return pair[columns].sort_values(["commodity", "expiry_near", "date"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 9. Liquidity and capacity
# --------------------------------------------------------------------------- #


def liquidity(basis: pd.DataFrame, lot_size_mt: float, rules: Rules) -> pd.DataFrame:
    """Daily volume and open interest of the active contract (ONE commodity), and tradeable size.

    Capacity = a fixed share of the rolling average daily volume: the size a
    desk could put on in a day without being most of the market.
    """
    active = basis.loc[basis["is_active"]].sort_values("date").copy()
    rs_per_mt = active["settlement_price"] * QUINTALS_PER_MT
    active["volume_lots"] = active["volume"] / lot_size_mt
    active["open_interest_lots"] = active["open_interest"] / lot_size_mt
    active["volume_rs_crore"] = active["volume"] * rs_per_mt / RS_PER_CRORE
    active["open_interest_rs_crore"] = active["open_interest"] * rs_per_mt / RS_PER_CRORE

    average_volume = active["volume"].rolling(rules.capacity_window_days, min_periods=1).mean()
    active["avg_volume_mt"] = average_volume
    active["capacity_lots"] = np.floor(average_volume * rules.capacity_share_of_volume / lot_size_mt)
    active["capacity_rs_crore"] = active["capacity_lots"] * lot_size_mt * rs_per_mt / RS_PER_CRORE
    columns = [
        "date", "commodity", "contract_symbol", "days_to_expiry", "volume", "open_interest", "volume_lots",
        "open_interest_lots", "volume_rs_crore", "open_interest_rs_crore", "avg_volume_mt", "capacity_lots", "capacity_rs_crore",
    ]  # fmt: skip
    return active[columns].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Signal finder: step-by-step calculator, best dates, and why they happened
# --------------------------------------------------------------------------- #

RANK_BY_ANNUALISED = "annualised"  # main table: annualised return, contracts with 15+ days to run
RANK_BY_RUPEES = "rupees"  # net Rs per quintal, any days to expiry
RANK_SHORT_DATED = "short_dated"  # annualised return, under 15 days to expiry (exaggerated)


def trade_calculator(spot, futures_price, days, tbill_pct, costs: CostInputs) -> dict:
    """Both trades on one day, line by line, in Rs per quintal.

    Returns {"carry": [...], "reverse": [...], "summary": {...}}. Each line is
    (label, formula in words, amount). Every amount comes from `cash_and_carry`,
    `reverse_arb` and `futures_leg_costs`, so the calculator can never disagree
    with the rest of the dashboard.
    """
    carry = cash_and_carry(spot, futures_price, days, tbill_pct, costs)
    reverse = reverse_arb(spot, futures_price, days, tbill_pct, costs)
    sell_leg = futures_leg_costs(futures_price, costs, buys_futures=False)
    buy_leg = futures_leg_costs(futures_price, costs, buys_futures=True)
    gst_note = f" + {costs.gst_pct:.0f}% GST" if costs.gst_on_storage_and_assaying else ""
    funding_rate = tbill_pct + costs.funding_spread_pct
    funding = funding_cost(spot, tbill_pct, days, costs)
    storage = storage_cost(days, costs)

    carry_lines = [
        ("Futures premium", "futures - spot (= minus the basis)", carry["cc_gross"]),
        ("Storage", f"Rs {costs.storage_rs_per_quintal_per_day:.2f} x {days} days{gst_note}", -carry["cc_storage"]),
        ("Assaying", f"Rs {costs.assaying_rs_per_quintal:.0f} per quintal, once{gst_note}", -carry["cc_assaying"]),
        ("Handling", f"Rs {costs.handling_rs_per_quintal:.0f} per quintal, once", -carry["cc_handling"]),
        ("Exchange fee", f"futures x Rs {costs.exchange_fee_rs_per_lakh} per lakh", -sell_leg["exchange_fee"]),
        ("SEBI fee", f"futures x Rs {costs.sebi_fee_rs_per_crore:.0f} per crore", -sell_leg["sebi_fee"]),
        ("Brokerage", f"futures x Rs {costs.brokerage_rs_per_lakh:.0f} per lakh", -sell_leg["brokerage"]),
        ("GST on fees", f"{costs.gst_pct:.0f}% of exchange fee and brokerage", -sell_leg["gst_on_fees"]),
        ("Stamp duty", "nil: futures are sold, duty is on the buyer", -sell_leg["stamp_duty"]),
        ("= Carry profit before funding", "gross - all costs above", carry["cc_carry_profit"]),
        ("Funding", f"spot x {funding_rate:.2f}% x {days}/365", -carry["cc_funding"]),
        ("= Net profit after funding", "carry profit - funding", carry["cc_net_profit"]),
    ]
    reverse_lines = [
        ("Basis", "spot - futures", reverse["rev_price_gap"]),
        ("Avoided funding", f"spot x {funding_rate:.2f}% x {days}/365", funding),
        ("Avoided storage", f"Rs {costs.storage_rs_per_quintal_per_day:.2f} x {days} days{gst_note}", storage),
        ("Exchange fee", f"futures x Rs {costs.exchange_fee_rs_per_lakh} per lakh", -buy_leg["exchange_fee"]),
        ("SEBI fee", f"futures x Rs {costs.sebi_fee_rs_per_crore:.0f} per crore", -buy_leg["sebi_fee"]),
        ("Brokerage", f"futures x Rs {costs.brokerage_rs_per_lakh:.0f} per lakh", -buy_leg["brokerage"]),
        ("GST on fees", f"{costs.gst_pct:.0f}% of exchange fee and brokerage", -buy_leg["gst_on_fees"]),
        ("Stamp duty", f"futures x {costs.stamp_duty_pct}% (buyer pays)", -buy_leg["stamp_duty"]),
        ("= Net benefit", "gap + avoided carry - transaction costs", reverse["rev_benefit"]),
    ]
    fair = fair_value(spot, days, tbill_pct, costs)
    return {
        "carry": carry_lines,
        "reverse": reverse_lines,
        "summary": {
            "carry_profit": carry["cc_carry_profit"],
            "carry_holding_pct": carry["cc_holding_return_pct"],
            "carry_annualised_pct": carry["cc_carry_return_pct"],
            "carry_excess_over_tbill": carry["cc_excess_over_tbill"],
            "carry_net_profit": carry["cc_net_profit"],
            "carry_after_funding_annualised_pct": carry["cc_return_after_funding_pct"],
            "implied_carry_pct": implied_carry(spot, futures_price, days),
            "cost_of_carry_pct": cost_of_carry(spot, days, tbill_pct, costs),
            "reverse_benefit": reverse["rev_benefit"],
            "reverse_holding_pct": reverse["rev_benefit_pct"],
            "reverse_annualised_pct": reverse["rev_return_pct"],
            "reverse_ex_funding_annualised_pct": reverse["rev_return_ex_funding_pct"],
            "reverse_excess_over_tbill": reverse["rev_excess_over_tbill"],  # ex-funding return minus T-bill
            "fair_value": fair,
            "mispricing_pct": (futures_price - fair) / spot * 100,
        },
    }


def signal_days(economics: pd.DataFrame, settlements: pd.DataFrame) -> pd.DataFrame:
    """Every tradeable day with the return of its tradeable side, one row per day (ONE commodity).

    net_rs          profit after ALL costs including funding (what makes a day tradeable)
    holding_pct     return over the holding period; for cash-and-carry this is before funding
    annualised_pct  holding_pct x 365 / days to expiry
    ex_funding_annualised_pct  the annualised return with interest left out, the figure
                    compared with the T-bill. For cash-and-carry that is annualised_pct
                    itself (already before funding); for reverse arb it excludes the
                    avoided funding, which would otherwise be counted twice.
    excess_over_tbill  ex_funding_annualised_pct minus the T-bill yield
    The last two columns show how the contract actually ended, once it has expired.
    """
    days = economics.loc[economics["tradeable_signal"] != SIGNAL_NONE].copy()
    is_carry = days["tradeable_signal"] == SIGNAL_CASH_AND_CARRY
    days["side"] = days["tradeable_signal"]
    days["net_rs"] = np.where(is_carry, days["cc_net_profit"], days["rev_benefit"])
    days["holding_pct"] = np.where(is_carry, days["cc_holding_return_pct"], days["rev_benefit_pct"])
    days["annualised_pct"] = np.where(is_carry, days["cc_carry_return_pct"], days["rev_return_pct"])
    days["ex_funding_annualised_pct"] = np.where(is_carry, days["cc_carry_return_pct"], days["rev_return_ex_funding_pct"])
    days["excess_over_tbill"] = days["ex_funding_annualised_pct"] - days["tbill_pct"]
    days["short_dated"] = days["days_to_expiry"] < SHORT_DATED_DAYS
    days = days.merge(settlements[["contract_symbol", "final_settlement", "convergence_error_pct"]], on="contract_symbol", how="left")
    columns = [
        "date", "commodity", "side", "contract_symbol", "days_to_expiry", "spot", "settlement_price", "basis_pct",
        "net_rs", "holding_pct", "annualised_pct", "ex_funding_annualised_pct", "excess_over_tbill", "tbill_pct", "short_dated",
        "final_settlement", "convergence_error_pct",
    ]  # fmt: skip
    return days[columns].rename(columns={"settlement_price": "futures"}).reset_index(drop=True)


def best_dates(days: pd.DataFrame, side: str, ranking: str = RANK_BY_ANNUALISED, top: int = 10) -> pd.DataFrame:
    """The best tradeable days for one side.

    RANK_BY_ANNUALISED  by annualised return, only days with 15+ days to expiry (the main table)
    RANK_BY_RUPEES      by net Rs per quintal, any days to expiry
    RANK_SHORT_DATED    by annualised return, under 15 days to expiry; exaggerated by annualisation
    """
    own = days.loc[days["side"] == side]
    if ranking == RANK_BY_ANNUALISED:
        ranked = own.loc[~own["short_dated"]].sort_values("annualised_pct", ascending=False)
    elif ranking == RANK_SHORT_DATED:
        ranked = own.loc[own["short_dated"]].sort_values("annualised_pct", ascending=False)
    else:
        ranked = own.sort_values("net_rs", ascending=False)
    return ranked.head(top).reset_index(drop=True)


def describe_best_dates(name: str, side: str, days: pd.DataFrame, economics: pd.DataFrame, arrival_months: list[int] | None = None) -> str:
    """One paragraph: when this side's returns peaked, what those days had in common, and how often they came.

    Three things are reported for the top 10 days (15+ days to expiry): how far
    from expiry they were, what spot had done over the previous five trading
    days, and how many fell in the crop's typical arrival season. These are
    things that were true on those days, not causes. The arrival months are
    general market knowledge held in the config, not exchange data.
    """
    label = "cash-and-carry" if side == SIGNAL_CASH_AND_CARRY else "reverse arb"
    top = best_dates(days, side, RANK_BY_ANNUALISED)
    own = days.loc[(days["side"] == side) & ~days["short_dated"]]
    if top.empty:
        return f"{name} had no tradeable {label} day with at least {SHORT_DATED_DAYS} days to expiry in this period."

    best = top.iloc[0]
    months = top["date"].dt.strftime("%b %Y").value_counts()
    month_text = f"{months.iloc[0]} of the top {len(top)} fell in {months.index[0]}"

    spot_move = economics.set_index("date")["spot"].pct_change(5) * 100
    moves = spot_move.reindex(top["date"]).dropna()
    typical_move = moves.median() if len(moves) else float("nan")
    if side == SIGNAL_CASH_AND_CARRY:
        move_reason = "futures ran ahead of spot" if typical_move >= 0 else "spot fell faster than futures"
    else:
        move_reason = "spot jumped and futures did not follow" if typical_move >= 0 else "futures fell further than spot"
    move_text = f"spot had moved {typical_move:+.1f}% over the previous five trading days ({move_reason})" if len(moves) else ""

    typical_dte, all_dte = top["days_to_expiry"].median(), own["days_to_expiry"].median()
    dte_text = f"they came with a median of {typical_dte:.0f} days to expiry, against {all_dte:.0f} for all tradeable {label} days"

    season_text = ""
    if arrival_months:
        in_season = int(top["date"].dt.month.isin(arrival_months).sum())
        season_text = f" {in_season} of the {len(top)} fell in the typical arrival season (general market knowledge, not from exchange data)."

    # How often: runs of consecutive trading days on which this side was tradeable.
    tradeable = (economics["tradeable_signal"] == side).reset_index(drop=True)
    runs = int((tradeable & ~tradeable.shift(fill_value=False)).sum())
    share = tradeable.mean() * 100
    return (
        f"{name} {label} peaked on {best['date']:%d %b %Y} at {best['annualised_pct']:.1f}% annualised "
        f"(Rs {best['net_rs']:,.0f} per quintal net, {best['days_to_expiry']} days to expiry). {month_text}; {dte_text}"
        + (f"; {move_text}." if move_text else ".")
        + season_text
        + f" This side was tradeable on {int(tradeable.sum())} days ({share:.0f}%), in {runs} separate windows averaging {tradeable.sum() / max(runs, 1):.1f} days each."
    )


# --------------------------------------------------------------------------- #
# 10. Snapshot and insights
# --------------------------------------------------------------------------- #


def signal_shares(economics: pd.DataFrame, column: str = "tradeable_signal") -> dict:
    """Percentage of days spent in each signal state (tradeable signal by default)."""
    shares = economics[column].value_counts(normalize=True) * 100
    return {signal: float(shares.get(signal, 0.0)) for signal in (SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE, SIGNAL_NONE)}


def describe_trade(day: pd.Series) -> str:
    """What the tradeable side earns on one day, in words.

    For a short-dated contract the rupee profit and the holding-period return
    lead, and the annualised figure is given as a caution, not a headline.
    """
    signal, days = day["tradeable_signal"], day["days_to_expiry"]
    if signal == SIGNAL_NONE:
        return "neither side makes money after all costs"
    if signal == SIGNAL_CASH_AND_CARRY:
        action, profit, holding, annual = "cash-and-carry locks in", day["cc_net_profit"], day["cc_net_profit_pct"], day["cc_carry_return_pct"]
        benchmark = f" against a T-bill yield of {day['tbill_pct']:.2f}%"
    else:
        action, profit, holding, annual = "a stock holder gains", day["rev_benefit"], day["rev_benefit_pct"], day["rev_return_pct"]
        benchmark = " by selling spot and buying futures"
    if days < SHORT_DATED_DAYS:
        return (
            f"{action} Rs {profit:,.0f} per quintal, {holding:.2f}% over {days} days{benchmark if signal == SIGNAL_REVERSE else ''} "
            f"({annual:.0f}% annualised, which exaggerates so short a trade)"
        )
    return f"{action} {annual:.1f}% annualised{benchmark}"


def build_insights(name: str, economics: pd.DataFrame, liquidity_table: pd.DataFrame) -> list[str]:
    """Three plain-English sentences for the dashboard header, generated from the data."""
    latest, shares = economics.iloc[-1], signal_shares(economics)
    capacity = liquidity_table.iloc[-1]

    history = (
        f"{name} offered a tradeable cash-and-carry on {shares[SIGNAL_CASH_AND_CARRY]:.0f}% of days and a tradeable "
        f"reverse arb on {shares[SIGNAL_REVERSE]:.0f}% of days; on the remaining {shares[SIGNAL_NONE]:.0f}% neither side paid after costs."
    )

    side = "above" if latest["mispricing"] >= 0 else "below"
    current = (
        f"On {latest['date']:%d %b %Y} the active contract ({latest['days_to_expiry']} days to expiry) is "
        f"Rs {abs(latest['mispricing']):,.0f} ({abs(latest['mispricing_pct']):.2f}%) {side} fair value: {describe_trade(latest)}."
    )

    if latest["break_even_storage"] > 0:
        level = (
            f"Break-even storage is Rs {latest['break_even_storage']:.2f} per quintal per day "
            f"against an actual Rs {latest['actual_storage']:.2f}, so futures pay for the warehouse with room to spare"
        )
    else:
        level = "Futures do not cover the other carry costs today, so no storage rate would make cash-and-carry pay"
    storage = f"{level}. About Rs {capacity['capacity_rs_crore']:.1f} crore a day is tradeable in the active contract."
    return [history, current, storage]


def snapshot(name: str, economics: pd.DataFrame, liquidity_table: pd.DataFrame) -> dict:
    """Latest-day values for the dashboard header (ONE commodity)."""
    latest = economics.iloc[-1]
    insights = build_insights(name, economics, liquidity_table)
    return {
        "commodity": latest["commodity"],
        "as_of": latest["date"],
        "spot": latest["spot"],
        "active_contract": latest["contract_symbol"],
        "active_futures": latest["settlement_price"],
        "days_to_expiry": latest["days_to_expiry"],
        "basis": latest["basis"],
        "basis_pct": latest["basis_pct"],
        "basis_ann_pct": latest["basis_ann_pct"],
        "fair_value": latest["fair_value"],
        "mispricing_pct": latest["mispricing_pct"],
        "signal": latest["signal"],
        "tradeable_signal": latest["tradeable_signal"],
        "rev_benefit": latest["rev_benefit"],
        "cc_net_profit": latest["cc_net_profit"],
        "cc_net_profit_pct": latest["cc_net_profit_pct"],
        "cc_return_after_funding_pct": latest["cc_return_after_funding_pct"],
        "implied_carry_pct": latest["implied_carry_pct"],
        "cost_of_carry_pct": latest["cost_of_carry_pct"],
        "rev_benefit_pct": latest["rev_benefit_pct"],
        "expiry": latest["expiry"],
        "cc_carry_return_pct": latest["cc_carry_return_pct"],
        "tbill_pct": latest["tbill_pct"],
        "tbill_is_rbi_fill": latest["tbill_is_rbi_fill"],
        "cc_excess_over_tbill": latest["cc_excess_over_tbill"],
        "rev_return_pct": latest["rev_return_pct"],
        "rev_return_ex_funding_pct": latest["rev_return_ex_funding_pct"],
        "rev_excess_over_tbill": latest["rev_excess_over_tbill"],
        "break_even_storage": latest["break_even_storage"],
        "actual_storage": latest["actual_storage"],
        "capacity_rs_crore": liquidity_table.iloc[-1]["capacity_rs_crore"],
        "insight_1": insights[0],
        "insight_2": insights[1],
        "insight_3": insights[2],
    }
