"""Metrics for the cash-futures arbitrage monitor.

Pure functions: DataFrames and inputs in, DataFrames out. Nothing here reads
files or the network, so the dashboard can call the same functions with the
user's own cost inputs and get fresh numbers instantly.

Conventions
  * All money amounts are Rs per quintal (the unit futures and spot are quoted in).
  * Percentages are in percent (5.5 means 5.5%), annualised on a 365-day year.
  * Only `settlement_price` is used; OHLC is 0 on days a contract did not trade.
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
        "cc_net_profit": net_profit,
        "cc_net_profit_pct": net_profit / spot * 100,
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
    avoided_carry = funding_cost(spot, tbill_pct, days, costs) + storage_cost(days, costs)
    benefit = spot - futures_price + avoided_carry - transaction
    return {
        "rev_price_gap": spot - futures_price,
        "rev_avoided_carry": avoided_carry,
        "rev_transaction": transaction,
        "rev_benefit": benefit,
        "rev_benefit_pct": benefit / spot * 100,
        "rev_return_pct": annualise(benefit / spot * 100, days),
    }


# --------------------------------------------------------------------------- #
# 4. Fair value and signal
# --------------------------------------------------------------------------- #


def fair_value(spot, days, tbill_pct, costs: CostInputs):
    """Cost-of-carry fair price of the future: spot plus funding and storage to expiry."""
    return spot + funding_cost(spot, tbill_pct, days, costs) + storage_cost(days, costs)


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

    basis = settlement - spot. `basis_vs_3d_avg` uses the 3-day average spot,
    the level the contract actually settles to at expiry.
    """
    table = mark_active_contract(futures, rules.roll_dte_threshold)
    table = table.merge(add_three_day_spot(spot)[["date", "commodity", "spot_3d_avg"]], on=["date", "commodity"], how="left")
    table["basis"] = table["settlement_price"] - table["spot"]
    table["basis_pct"] = table["basis"] / table["spot"] * 100
    table["basis_ann_pct"] = annualise(table["basis_pct"], table["days_to_expiry"])
    table["basis_vs_3d_avg_pct"] = (table["settlement_price"] / table["spot_3d_avg"] - 1) * 100
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
    final = final.assign(convergence_error_pct=final["basis_vs_3d_avg_pct"], same_day_gap_pct=final["basis_pct"])
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

    The mean shows direction (do futures approach spot from above or below);
    the mean absolute value shows how tight the basis is, whatever its sign.
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
        "settlement_price", "spot", "basis_pct", "open_interest",
    ]  # fmt: skip
    return curve[columns].rename(columns={"basis_pct": "pct_vs_spot"})


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
    """Calendar-month averages of the curve slope, to show the harvest-cycle pattern."""
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
# 10. Snapshot and insights
# --------------------------------------------------------------------------- #


def signal_shares(economics: pd.DataFrame, column: str = "tradeable_signal") -> dict:
    """Percentage of days spent in each signal state (tradeable signal by default)."""
    shares = economics[column].value_counts(normalize=True) * 100
    return {signal: float(shares.get(signal, 0.0)) for signal in (SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE, SIGNAL_NONE)}


def build_insights(name: str, economics: pd.DataFrame, liquidity_table: pd.DataFrame) -> list[str]:
    """Three plain-English sentences for the dashboard header, generated from the data."""
    latest, shares = economics.iloc[-1], signal_shares(economics)
    capacity = liquidity_table.iloc[-1]

    history = (
        f"{name} offered a tradeable cash-and-carry on {shares[SIGNAL_CASH_AND_CARRY]:.0f}% of days and a tradeable "
        f"reverse arb on {shares[SIGNAL_REVERSE]:.0f}% of days; on the remaining {shares[SIGNAL_NONE]:.0f}% neither side paid after costs."
    )

    side = "above" if latest["mispricing"] >= 0 else "below"
    today = {
        SIGNAL_CASH_AND_CARRY: f"cash-and-carry locks in {latest['cc_carry_return_pct']:.1f}% annualised against a T-bill yield of {latest['tbill_pct']:.2f}%",
        SIGNAL_REVERSE: f"a stock holder gains {latest['rev_return_pct']:.1f}% annualised by selling spot and buying futures",
        SIGNAL_NONE: "neither side makes money after all costs",
    }[latest["tradeable_signal"]]
    current = (
        f"Today the active contract ({latest['days_to_expiry']} days to expiry) is Rs {abs(latest['mispricing']):,.0f} "
        f"({abs(latest['mispricing_pct']):.2f}%) {side} fair value: {today}."
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
        "cc_carry_return_pct": latest["cc_carry_return_pct"],
        "tbill_pct": latest["tbill_pct"],
        "tbill_is_rbi_fill": latest["tbill_is_rbi_fill"],
        "cc_excess_over_tbill": latest["cc_excess_over_tbill"],
        "rev_return_pct": latest["rev_return_pct"],
        "break_even_storage": latest["break_even_storage"],
        "actual_storage": latest["actual_storage"],
        "capacity_rs_crore": liquidity_table.iloc[-1]["capacity_rs_crore"],
        "insight_1": insights[0],
        "insight_2": insights[1],
        "insight_3": insights[2],
    }
