"""Historical backtest of the cash-and-carry and reverse-arb trades.

Event-driven and free of look-ahead: the engine walks forward one trading day
at a time and every decision uses only that day's row and earlier ones.

What is ACTUAL and what is MODELLED
  actual    futures entry and exit prices (NCDEX daily settlement prices), the final
            settlement price at expiry, the polled spot price, volume and open interest
  modelled  that the desk can buy or sell physical goods at the polled spot price,
            that it trades at the daily close, every cost line (from the cost model),
            the futures margin, the capital buffer, slippage and transport

Units: prices are Rs per quintal. One lot = lot size in MT x 10 quintals.
"""

from dataclasses import dataclass, field
from itertools import groupby

import numpy as np
import pandas as pd

from src import metrics
from src.metrics import DAYS_IN_YEAR, QUINTALS_PER_MT, SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE, CostInputs

EXIT_AT_EXPIRY = "expiry"  # hold to expiry and deliver (or take delivery) at the final settlement price
EXIT_EARLY = "early"  # close both legs N days before expiry at that day's settlement and spot

COST_LINES = ["funding", "storage", "assaying", "handling", "fees", "gst", "stamp_duty", "slippage", "transport"]


@dataclass(frozen=True)
class BacktestConfig:
    """Every backtest input. Defaults live in config/assumptions.toml."""

    threshold_pct: float = 12.0  # enter when the quoted annualised return (before funding) is at least this
    min_dte: int = 15  # ignore contracts with fewer days to expiry: annualising exaggerates them
    exit_mode: str = EXIT_AT_EXPIRY
    exit_days_before: int = 5  # used by EXIT_EARLY
    starting_capital: float = 2e7  # Rs
    max_allocation_pct: float = 25.0  # of starting capital, per trade
    max_positions: int = 4  # open at the same time
    margin_pct: float = 15.0  # futures margin as % of futures value (assumption)
    buffer_pct: float = 2.0  # spare cash held against margin calls, % of spot value (assumption)
    volume_cap_pct: float = 10.0  # lots capped at this share of the contract's 20-day average volume
    volume_window: int = 20
    inventory_lots: int = 30  # stock the desk already owns, for reverse arb only
    slippage_pct: float = 0.0  # of price, on each leg traded (assumption)
    transport_rs_per_quintal: float = 0.0  # mandi to warehouse (assumption)
    sides: tuple[str, ...] = (SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE)


@dataclass
class BacktestResult:
    trades: pd.DataFrame  # one row per executed trade
    equity: pd.DataFrame  # one row per trading day
    opportunities: pd.DataFrame  # one row per run of consecutive qualifying days on a contract
    summary: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Data preparation
# --------------------------------------------------------------------------- #


def prepare_contract_days(basis: pd.DataFrame, tbill: pd.DataFrame, costs: CostInputs, config: BacktestConfig) -> pd.DataFrame:
    """Every contract on every day (ONE commodity) with the quoted return of each side.

    `avg_volume` is the contract's own rolling average volume up to and including
    that day, so it never looks ahead.
    """
    days = basis.merge(tbill.rename(columns={"yield_pct": "tbill_pct"})[["date", "tbill_pct"]], on="date", how="left")
    days = days.sort_values(["contract_symbol", "date"]).reset_index(drop=True)
    days["avg_volume"] = days.groupby("contract_symbol")["volume"].transform(lambda volume: volume.rolling(config.volume_window, min_periods=1).mean())

    spot, price, dte, rate = days["spot"], days["settlement_price"], days["days_to_expiry"], days["tbill_pct"]
    carry = metrics.cash_and_carry(spot, price, dte, rate, costs)
    reverse = metrics.reverse_arb(spot, price, dte, rate, costs)
    days["carry_quoted_pct"] = carry["cc_carry_return_pct"]  # annualised, before funding
    days["carry_net_profit"] = carry["cc_net_profit"]  # Rs per quintal, after funding
    days["reverse_quoted_pct"] = reverse["rev_return_ex_funding_pct"]  # annualised, excluding avoided funding
    days["reverse_benefit"] = reverse["rev_benefit"]
    return days.sort_values(["date", "expiry"]).reset_index(drop=True)


def qualifies(day_rows: pd.DataFrame, side: str, config: BacktestConfig) -> pd.Series:
    """Which contracts offer this side today: enough days left, liquid, profitable, and above the threshold."""
    quoted, profit = (
        (day_rows["carry_quoted_pct"], day_rows["carry_net_profit"]) if side == SIGNAL_CASH_AND_CARRY else (day_rows["reverse_quoted_pct"], day_rows["reverse_benefit"])
    )
    liquid = (day_rows["open_interest"] > 0) & (day_rows["volume"] > 0)
    return liquid & (day_rows["days_to_expiry"] >= config.min_dte) & (quoted >= config.threshold_pct) & (profit > 0)


# --------------------------------------------------------------------------- #
# One trade
# --------------------------------------------------------------------------- #


def capital_per_quintal(spot: float, futures: float, costs: CostInputs, config: BacktestConfig) -> float:
    """Cash a cash-and-carry ties up per quintal: the goods, one-off warehouse costs, futures margin and a buffer."""
    one_off = (costs.assaying_rs_per_quintal * costs.warehouse_gst_multiplier) + costs.handling_rs_per_quintal + config.transport_rs_per_quintal
    return spot + one_off + futures * config.margin_pct / 100 + spot * config.buffer_pct / 100


def trade_costs(position: dict, days_held: int, exit_futures: float, exit_spot: float, delivered: bool, costs: CostInputs, config: BacktestConfig) -> dict:
    """Every cost line of one trade so far, in Rs for the whole position (not per quintal).

    Storage is charged for the days actually held and funding on the capital
    actually used. A trade closed early pays a second futures leg and slippage
    on both exit legs; a delivered trade does not.
    """
    quantity, carry = position["quantity"], position["side"] == SIGNAL_CASH_AND_CARRY
    entry_leg = metrics.futures_leg_costs(position["entry_futures"], costs, buys_futures=not carry)
    exit_leg = {} if delivered else metrics.futures_leg_costs(exit_futures, costs, buys_futures=carry)

    def leg_total(name: str) -> float:
        return float(entry_leg[name] + exit_leg.get(name, 0.0))

    slipped = position["entry_spot"] + position["entry_futures"] + (0 if delivered else exit_spot + exit_futures)
    lines = {name: 0.0 for name in COST_LINES}
    lines["fees"] = (leg_total("exchange_fee") + leg_total("sebi_fee") + leg_total("brokerage")) * quantity
    lines["stamp_duty"] = leg_total("stamp_duty") * quantity
    lines["slippage"] = slipped * config.slippage_pct / 100 * quantity
    gst_on_fees = leg_total("gst_on_fees") * quantity
    if carry:
        warehouse_gst = costs.warehouse_gst_multiplier - 1
        lines["storage"] = costs.storage_rs_per_quintal_per_day * days_held * quantity
        lines["assaying"] = costs.assaying_rs_per_quintal * quantity
        lines["handling"] = costs.handling_rs_per_quintal * quantity
        lines["transport"] = config.transport_rs_per_quintal * quantity
        lines["gst"] = gst_on_fees + (lines["storage"] + lines["assaying"]) * warehouse_gst
        lines["funding"] = position["capital_used"] * position["funding_rate_pct"] / 100 * days_held / DAYS_IN_YEAR
    else:
        lines["gst"] = gst_on_fees
    return lines


def position_value(position: dict, futures: float, spot: float, days_held: int, delivered: bool, costs: CostInputs, config: BacktestConfig) -> dict:
    """Profit and loss of a position at the given prices, in Rs.

    Cash-and-carry: (entry futures - futures now) on the short futures, plus
    (spot now - entry spot) on the goods. At delivery the goods are handed over
    at the final settlement price, so `spot` is that price and the total is
    simply entry futures - entry spot.
    Reverse arb mirrors it, and adds the storage and interest the stock holder
    saves while the stock is out of the warehouse.
    """
    quantity, carry = position["quantity"], position["side"] == SIGNAL_CASH_AND_CARRY
    direction = 1 if carry else -1
    gross = direction * ((position["entry_futures"] - futures) + (spot - position["entry_spot"])) * quantity
    lines = trade_costs(position, days_held, futures, spot, delivered, costs, config)
    saved_storage = saved_interest = 0.0
    if not carry:
        saved_storage = metrics.storage_cost(days_held, costs) * quantity
        saved_interest = position["capital_used"] * position["funding_rate_pct"] / 100 * days_held / DAYS_IN_YEAR
    total_costs = sum(lines.values())
    return {
        "gross_pnl": gross,
        "saved_storage": saved_storage,
        "saved_interest": saved_interest,
        "total_costs": total_costs,
        "net_pnl": gross + saved_storage + saved_interest - total_costs,
        **{f"cost_{name}": value for name, value in lines.items()},
    }


def close_trade(position: dict, row, delivered: bool, costs: CostInputs, config: BacktestConfig) -> dict:
    """Turn an open position into a finished trade record. `row` is that contract's record on the exit day."""
    days_held = int((row.date - position["entry_date"]).days)
    # At delivery both legs meet at the final settlement price; an early exit uses that day's spot.
    exit_spot = row.settlement_price if delivered else row.spot
    value = position_value(position, row.settlement_price, exit_spot, days_held, delivered, costs, config)
    capital = position["capital_used"]
    holding = value["net_pnl"] / capital * 100
    funding_effect = value["cost_funding"] - value["saved_interest"]  # what financing took (carry) or added (reverse)
    return {
        **{key: position[key] for key in ("side", "contract_symbol", "expiry", "entry_date", "lots", "quantity", "entry_spot", "entry_futures", "quoted_yield_pct", "capital_used", "funding_rate_pct")},
        "exit_date": row.date,
        "exit_type": "delivery at final settlement" if delivered else f"closed {config.exit_days_before} days before expiry",
        "exit_futures": row.settlement_price,
        "exit_spot": row.spot,
        "holding_days": days_held,
        **value,
        "net_pnl_before_funding": value["net_pnl"] + funding_effect,
        "holding_return_pct": holding,
        "annualised_return_pct": holding * DAYS_IN_YEAR / days_held if days_held > 0 else np.nan,
        "annualised_before_funding_pct": (value["net_pnl"] + funding_effect) / capital * 100 * DAYS_IN_YEAR / days_held if days_held > 0 else np.nan,
        "price_source": "futures: actual settlement prices; spot: polled reference price, assumed tradable",
    }


# --------------------------------------------------------------------------- #
# The event loop
# --------------------------------------------------------------------------- #


def run_backtest(days: pd.DataFrame, costs: CostInputs, lot_size_mt: float, config: BacktestConfig) -> BacktestResult:
    """Walk forward through `days` (from `prepare_contract_days`) and trade the signals.

    Each day, in order: close positions that have reached their exit, look for
    new entries with the cash and stock that is free, then mark what is open.
    Capital is locked from entry to exit and is never used twice.

    The "does this contract qualify today" test is worked out for all rows up
    front. That is not look-ahead: each row's answer depends only on that row.
    """
    lot_quintals = lot_size_mt * QUINTALS_PER_MT
    days = days.assign(carry_ok=qualifies(days, SIGNAL_CASH_AND_CARRY, config), reverse_ok=qualifies(days, SIGNAL_REVERSE, config))
    side_columns = {SIGNAL_CASH_AND_CARRY: ("carry_ok", "carry_quoted_pct"), SIGNAL_REVERSE: ("reverse_ok", "reverse_quoted_pct")}

    free_cash = config.starting_capital
    free_inventory = config.inventory_lots * lot_quintals
    open_positions: dict[str, dict] = {}
    done: set[tuple[str, str]] = set()  # (contract, side) already traded: one trade per contract and side
    in_run: dict[tuple[str, str], dict] = {}  # opportunities still running, keyed by (contract, side)
    trades, opportunities, equity_rows, opportunity_dates, realised = [], [], [], set(), 0.0

    # One pass over the rows as light records, grouped by date (far faster than slicing the frame each day).
    needed = [
        "date", "contract_symbol", "expiry", "days_to_expiry", "spot", "settlement_price", "tbill_pct", "avg_volume",
        "carry_quoted_pct", "reverse_quoted_pct", "carry_ok", "reverse_ok",
    ]  # fmt: skip
    records = days.sort_values(["date", "expiry"])[needed].itertuples(index=False, name="ContractDay")
    for date, day_records in groupby(records, key=lambda record: record.date):
        rows = list(day_records)
        by_contract = {row.contract_symbol: row for row in rows}

        # 1. Exits
        for symbol in [symbol for symbol in open_positions if symbol in by_contract]:
            position, row = open_positions[symbol], by_contract[symbol]
            at_expiry = row.days_to_expiry <= 0
            early = config.exit_mode == EXIT_EARLY and row.days_to_expiry <= config.exit_days_before
            if at_expiry or early:
                trade = close_trade(position, row, delivered=at_expiry or config.exit_mode == EXIT_AT_EXPIRY, costs=costs, config=config)
                trades.append(trade)
                realised += trade["net_pnl"]
                if position["side"] == SIGNAL_CASH_AND_CARRY:
                    free_cash += position["capital_used"]
                else:
                    free_inventory += position["quantity"]
                del open_positions[symbol]

        # 2. Entries and opportunity tracking
        for side in config.sides:
            ok_column, quoted_column = side_columns[side]
            candidates = sorted((row for row in rows if getattr(row, ok_column)), key=lambda row: getattr(row, quoted_column), reverse=True)
            qualifying = {row.contract_symbol for row in candidates}
            for key in [key for key in in_run if key[1] == side and key[0] not in qualifying]:
                opportunities.append(in_run.pop(key))  # the run of qualifying days has ended
            if candidates:
                opportunity_dates.add(date)
            for row in candidates:
                key = (row.contract_symbol, side)
                quoted = getattr(row, quoted_column)
                run = in_run.setdefault(key, {"contract_symbol": row.contract_symbol, "side": side, "first_date": date, "days": 0, "quoted_sum": 0.0, "traded": False, "skipped": ""})
                run["last_date"], run["days"], run["quoted_sum"] = date, run["days"] + 1, run["quoted_sum"] + quoted
                if run["traded"]:
                    continue
                if key in done or row.contract_symbol in open_positions:
                    run["skipped"] = "contract already traded"
                    continue

                liquidity_lots = int(row.avg_volume * config.volume_cap_pct / 100 // lot_size_mt)
                if side == SIGNAL_CASH_AND_CARRY:
                    per_lot = capital_per_quintal(row.spot, row.settlement_price, costs, config) * lot_quintals
                    budget = min(free_cash, config.starting_capital * config.max_allocation_pct / 100)
                    lots = min(int(budget // per_lot), liquidity_lots)
                    reason = "no free capital" if budget < per_lot else "liquidity cap below one lot"
                else:
                    per_lot = row.spot * lot_quintals  # inventory value released by selling one lot at spot
                    lots = min(int(free_inventory // lot_quintals), liquidity_lots)
                    reason = "no free inventory" if free_inventory < lot_quintals else "liquidity cap below one lot"
                if len(open_positions) >= config.max_positions:
                    lots, reason = 0, "maximum simultaneous positions reached"
                if lots < 1:
                    run["skipped"] = reason
                    continue

                quantity = lots * lot_quintals
                position = {
                    "side": side, "contract_symbol": row.contract_symbol, "expiry": row.expiry, "entry_date": date,
                    "lots": lots, "quantity": quantity, "entry_spot": row.spot, "entry_futures": row.settlement_price,
                    "quoted_yield_pct": quoted, "capital_used": per_lot * lots,
                    "funding_rate_pct": row.tbill_pct + costs.funding_spread_pct,  # fixed at entry: known that day
                }  # fmt: skip
                open_positions[row.contract_symbol] = position
                done.add(key)
                run["traded"], run["skipped"] = True, ""
                if side == SIGNAL_CASH_AND_CARRY:
                    free_cash -= position["capital_used"]
                else:
                    free_inventory -= quantity

        # 3. Mark to market at today's settlement and spot
        unrealised = 0.0
        for symbol, position in open_positions.items():
            if symbol in by_contract:
                row = by_contract[symbol]
                position["mark"] = position_value(position, row.settlement_price, row.spot, int((date - position["entry_date"]).days), False, costs, config)["net_pnl"]
            unrealised += position.get("mark", 0.0)
        carry_capital = sum(p["capital_used"] for p in open_positions.values() if p["side"] == SIGNAL_CASH_AND_CARRY)
        inventory_out = sum(p["capital_used"] for p in open_positions.values() if p["side"] == SIGNAL_REVERSE)
        equity_rows.append(
            {
                "date": date, "realised_pnl": realised, "unrealised_pnl": unrealised, "equity": config.starting_capital + realised + unrealised,
                "capital_deployed": carry_capital, "inventory_released": inventory_out, "open_positions": len(open_positions),
            }  # fmt: skip
        )

    opportunities.extend(in_run.values())
    trades_table = pd.DataFrame(trades)
    equity = pd.DataFrame(equity_rows)
    opportunity_table = pd.DataFrame(opportunities)
    if not opportunity_table.empty:
        opportunity_table["avg_quoted_pct"] = opportunity_table["quoted_sum"] / opportunity_table["days"]
        opportunity_table = opportunity_table.drop(columns="quoted_sum")
    open_table = pd.DataFrame(list(open_positions.values()))
    summary = summarise(trades_table, equity, opportunity_table, days, lot_quintals, config, open_table, len(opportunity_dates))
    return BacktestResult(trades_table, equity, opportunity_table, summary)


# --------------------------------------------------------------------------- #
# Portfolio summary
# --------------------------------------------------------------------------- #


def max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """Largest fall from a previous peak, in Rs and as a % of that peak."""
    if equity.empty:
        return 0.0, 0.0
    peak = equity.cummax()
    fall = peak - equity
    worst_peak = peak.loc[fall.idxmax()]
    return float(fall.max()), float(fall.max() / worst_peak * 100) if worst_peak > 0 else 0.0


def summarise(
    trades: pd.DataFrame, equity: pd.DataFrame, opportunities: pd.DataFrame, days: pd.DataFrame, lot_quintals: float,
    config: BacktestConfig, open_positions: pd.DataFrame, opportunity_days: int,
) -> dict:  # fmt: skip
    """Portfolio figures. Returns are computed from rupees and capital, never by adding daily yields.

    return on deployed capital  net profit / capital-days in use x 365: what the money earned while it was working
    return on total capital     net profit / the whole capital base, annualised over the period: what the desk earned,
                                idle cash included
    The capital base is the starting cash for cash-and-carry, plus the value of the
    starting inventory (at the first day's spot) when reverse arb is switched on.

    Each return is given AFTER funding (interest charged on cash used, interest
    earned on cash released by reverse arb) and BEFORE funding (that interest
    taken out), the figure to compare with the T-bill.
    Positions still open on the last day are not in the trade list; their
    mark-to-market is in the equity curve only.
    """
    period_days = max(int((days["date"].max() - days["date"].min()).days), 1)
    first_spot = float(days.sort_values("date")["spot"].iloc[0])
    uses_cash, uses_stock = SIGNAL_CASH_AND_CARRY in config.sides, SIGNAL_REVERSE in config.sides
    inventory_value = config.inventory_lots * lot_quintals * first_spot if uses_stock else 0.0
    capital_base = (config.starting_capital if uses_cash else 0.0) + inventory_value

    # Capital-days: each day's deployed amount held until the next trading day.
    gaps = equity["date"].diff().shift(-1).dt.days.fillna(1) if not equity.empty else pd.Series(dtype=float)
    in_use = equity["capital_deployed"] + equity["inventory_released"] if not equity.empty else pd.Series(dtype=float)
    capital_days = float((in_use * gaps).sum())
    net = float(trades["net_pnl"].sum()) if not trades.empty else 0.0
    drawdown_rs, drawdown_pct = max_drawdown(equity["equity"] - config.starting_capital + capital_base) if not equity.empty else (0.0, 0.0)

    qualifying_days = int(opportunities["days"].sum()) if not opportunities.empty else 0
    quoted_average = float((opportunities["avg_quoted_pct"] * opportunities["days"]).sum() / qualifying_days) if qualifying_days else float("nan")
    funding_paid = float(trades["cost_funding"].sum()) if not trades.empty else 0.0
    interest_saved = float(trades["saved_interest"].sum()) if not trades.empty else 0.0
    net_before_funding = net + funding_paid - interest_saved

    cost_columns = [column for column in trades.columns if column.startswith("cost_")] if not trades.empty else []
    return {
        "trading_days": int(days["date"].nunique()),
        "opportunity_days": opportunity_days,
        "contract_days_qualifying": qualifying_days,
        "independent_opportunities": len(opportunities),
        "trades_executed": len(trades),
        "trades_still_open": len(open_positions),
        "capital_in_open_trades": float(open_positions["capital_used"].sum()) if not open_positions.empty else 0.0,
        "unrealised_pnl": float(equity["unrealised_pnl"].iloc[-1]) if not equity.empty else 0.0,
        "opportunities_not_traded": int((~opportunities["traded"]).sum()) if not opportunities.empty else 0,
        "wins": int((trades["net_pnl"] > 0).sum()) if not trades.empty else 0,
        "losses": int((trades["net_pnl"] <= 0).sum()) if not trades.empty else 0,
        "total_net_profit": net,
        "net_profit_before_funding": net_before_funding,
        "funding_paid": funding_paid,
        "interest_saved": interest_saved,
        "total_gross_profit": float(trades["gross_pnl"].sum()) if not trades.empty else 0.0,
        "total_costs": float(trades["total_costs"].sum()) if not trades.empty else 0.0,
        "costs_by_line": {column.removeprefix("cost_"): float(trades[column].sum()) for column in cost_columns},
        "capital_base": capital_base,
        "inventory_value": inventory_value,
        "peak_capital_deployed": float(in_use.max()) if len(in_use) else 0.0,
        "avg_capital_per_trade": float(trades["capital_used"].mean()) if not trades.empty else 0.0,
        "capital_utilisation_pct": capital_days / (capital_base * period_days) * 100 if capital_base else 0.0,
        "return_on_deployed_pct": net / capital_days * DAYS_IN_YEAR * 100 if capital_days else float("nan"),
        "return_on_total_pct": net / capital_base * 100 if capital_base else float("nan"),
        "annualised_return_on_total_pct": net / capital_base * DAYS_IN_YEAR / period_days * 100 if capital_base else float("nan"),
        "return_on_deployed_before_funding_pct": net_before_funding / capital_days * DAYS_IN_YEAR * 100 if capital_days else float("nan"),
        "annualised_return_on_total_before_funding_pct": net_before_funding / capital_base * DAYS_IN_YEAR / period_days * 100 if capital_base else float("nan"),
        "max_drawdown_rs": drawdown_rs,
        "max_drawdown_pct": drawdown_pct,
        "avg_quoted_yield_pct": quoted_average,
        "avg_holding_days": float(trades["holding_days"].mean()) if not trades.empty else float("nan"),
        "period_days": period_days,
    }
