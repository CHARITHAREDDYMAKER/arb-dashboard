"""Tests for the backtest engine, on small hand-built price paths.

The paths use one contract ("C1", expiring 30 days after the first date), a
3 MT lot (30 quintals) and round cost inputs so every figure can be checked on paper.
"""

import numpy as np
import pandas as pd
import pytest

from src import backtest
from src.backtest import BacktestConfig
from src.metrics import SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE, CostInputs

COSTS = CostInputs(
    storage_rs_per_quintal_per_day=1.0,
    assaying_rs_per_quintal=50.0,
    handling_rs_per_quintal=10.0,
    brokerage_rs_per_lakh=10.0,
    funding_spread_pct=1.0,
    exchange_fee_rs_per_lakh=5.0,
    sebi_fee_rs_per_crore=0.0,
    stamp_duty_pct=0.002,
    gst_pct=18.0,
    gst_on_storage_and_assaying=False,
)
LOT_MT = 3
CONFIG = BacktestConfig(
    threshold_pct=12.0, min_dte=15, starting_capital=1_000_000.0, max_allocation_pct=100.0, max_positions=4,
    margin_pct=10.0, buffer_pct=0.0, volume_cap_pct=10.0, inventory_lots=2, sides=(SIGNAL_CASH_AND_CARRY,),
)  # fmt: skip
START = pd.Timestamp("2026-01-01")
EXPIRY = START + pd.Timedelta(days=30)


def make_days(prices: list[tuple[int, float, float]], symbol: str = "C1", expiry: pd.Timestamp = EXPIRY, volume: float = 3000.0) -> pd.DataFrame:
    """Contract-days from (day offset, spot, futures) triples, shaped like `basis_table` output."""
    rows = [
        {
            "date": START + pd.Timedelta(days=offset), "commodity": "JEERA", "contract_symbol": symbol, "expiry": expiry,
            "days_to_expiry": (expiry - START).days - offset, "spot": spot, "settlement_price": futures,
            "volume": volume, "open_interest": 1000.0,
        }
        for offset, spot, futures in prices
    ]  # fmt: skip
    return pd.DataFrame(rows)


def run(prices_or_days, config: BacktestConfig = CONFIG, tbill_pct: float = 5.0) -> backtest.BacktestResult:
    days = prices_or_days if isinstance(prices_or_days, pd.DataFrame) else make_days(prices_or_days)
    tbill = pd.DataFrame({"date": sorted(days["date"].unique()), "yield_pct": tbill_pct})
    prepared = backtest.prepare_contract_days(days, tbill, COSTS, config)
    return backtest.run_backtest(prepared, COSTS, LOT_MT, config)


# Futures 400 above a spot of 20,000 with 30 days left: about 22% a year before funding, so it qualifies.
PROFITABLE = [(0, 20_000.0, 20_400.0), (1, 20_050.0, 20_420.0), (2, 20_100.0, 20_430.0), (30, 20_300.0, 20_300.0)]


# ---- a full trade, checked by hand ------------------------------------------ #


def test_hand_calculated_trade_held_to_delivery():
    result = run(PROFITABLE)
    assert len(result.trades) == 1
    trade = result.trades.iloc[0]

    # Sizing. Capital per quintal = spot 20,000 + assaying 50 + handling 10 + margin 10% x 20,400 = 22,100.
    # Per lot of 30 quintals = 663,000. Capital 1,000,000 buys 1 lot. Liquidity cap = 10% x 3,000 MT / 3 MT = 100 lots.
    assert trade["lots"] == 1 and trade["quantity"] == 30
    assert trade["capital_used"] == pytest.approx(663_000.0)
    assert trade["entry_date"] == START and trade["exit_date"] == EXPIRY and trade["holding_days"] == 30

    # Gross: sold futures at 20,400, bought spot at 20,000, delivered at the final settlement 20,300.
    # Futures leg (20,400 - 20,300) + goods (20,300 - 20,000) = 400 per quintal x 30 = 12,000.
    assert trade["gross_pnl"] == pytest.approx(12_000.0)

    # Costs for 30 quintals held 30 days.
    assert trade["cost_storage"] == pytest.approx(1.0 * 30 * 30)  # 900
    assert trade["cost_assaying"] == pytest.approx(50.0 * 30)  # 1,500
    assert trade["cost_handling"] == pytest.approx(10.0 * 30)  # 300
    assert trade["cost_fees"] == pytest.approx((20_400 * 5 / 1e5 + 20_400 * 10 / 1e5) * 30)  # (1.02 + 2.04) x 30 = 91.80
    assert trade["cost_gst"] == pytest.approx(91.80 * 0.18)  # 16.524, on fees only (GST on storage is off)
    assert trade["cost_stamp_duty"] == 0  # futures were sold and delivered: no purchase
    assert trade["cost_funding"] == pytest.approx(663_000 * 0.06 * 30 / 365)  # T-bill 5% + 1% on capital used = 3,269.59
    assert trade["cost_slippage"] == 0 and trade["cost_transport"] == 0

    total_costs = 900 + 1_500 + 300 + 91.80 + 16.524 + 663_000 * 0.06 * 30 / 365
    assert trade["total_costs"] == pytest.approx(total_costs)
    assert trade["net_pnl"] == pytest.approx(12_000 - total_costs)  # 5,922.09
    assert round(trade["net_pnl"]) == 5_922

    # Returns on the capital actually used.
    assert trade["holding_return_pct"] == pytest.approx(trade["net_pnl"] / 663_000 * 100)
    assert trade["annualised_return_pct"] == pytest.approx(trade["net_pnl"] / 663_000 * 100 * 365 / 30)  # about 10.87%
    assert result.summary["total_net_profit"] == pytest.approx(trade["net_pnl"])
    assert result.equity["equity"].iloc[-1] == pytest.approx(1_000_000 + trade["net_pnl"])


def test_delivery_profit_does_not_depend_on_where_the_contract_settles():
    low = run(PROFITABLE[:-1] + [(30, 18_000.0, 18_000.0)]).trades.iloc[0]
    high = run(PROFITABLE[:-1] + [(30, 23_000.0, 23_000.0)]).trades.iloc[0]
    assert low["net_pnl"] == pytest.approx(high["net_pnl"])  # the spread was locked on day one


def test_early_exit_uses_that_days_prices_and_pays_a_second_leg():
    config = BacktestConfig(**{**CONFIG.__dict__, "exit_mode": backtest.EXIT_EARLY, "exit_days_before": 5})
    prices = PROFITABLE[:-1] + [(25, 20_200.0, 20_250.0), (30, 20_300.0, 20_300.0)]
    trade = run(prices, config).trades.iloc[0]
    assert trade["holding_days"] == 25 and trade["exit_futures"] == 20_250.0 and trade["exit_spot"] == 20_200.0
    # Futures leg (20,400 - 20,250) + goods (20,200 - 20,000) = 350 per quintal x 30 = 10,500.
    assert trade["gross_pnl"] == pytest.approx(10_500.0)
    assert trade["cost_storage"] == pytest.approx(1.0 * 25 * 30)  # 25 days actually held
    assert trade["cost_stamp_duty"] == pytest.approx(20_250 * 0.002 / 100 * 30)  # the short is bought back: 12.15


# ---- one opportunity, one trade ---------------------------------------------- #


def test_consecutive_signal_days_make_one_opportunity_and_one_trade():
    result = run(PROFITABLE)  # the signal is on for three days running
    assert result.summary["contract_days_qualifying"] == 3
    assert result.summary["independent_opportunities"] == 1
    assert result.summary["trades_executed"] == 1
    assert result.opportunities.iloc[0]["days"] == 3 and bool(result.opportunities.iloc[0]["traded"])


def test_capital_is_locked_until_exit_and_never_used_twice():
    # Two contracts qualify on the same day; the capital only covers one lot of one of them.
    first = make_days(PROFITABLE)
    second = make_days([(0, 20_000.0, 20_500.0), (1, 20_050.0, 20_520.0), (2, 20_100.0, 20_530.0), (40, 20_300.0, 20_300.0)], symbol="C2", expiry=START + pd.Timedelta(days=40))
    result = run(pd.concat([first, second], ignore_index=True))
    assert result.summary["trades_executed"] == 1
    assert result.summary["opportunities_not_traded"] == 1
    assert "no free capital" in set(result.opportunities["skipped"])
    assert (result.equity["capital_deployed"] <= CONFIG.starting_capital).all()

    # With enough capital for both, both are taken, and deployed capital is the sum of the two.
    rich = BacktestConfig(**{**CONFIG.__dict__, "starting_capital": 2_000_000.0, "max_allocation_pct": 50.0})
    both = run(pd.concat([first, second], ignore_index=True), rich)
    assert both.summary["trades_executed"] == 2
    assert both.equity["capital_deployed"].max() == pytest.approx(both.trades["capital_used"].sum())
    assert both.equity["capital_deployed"].max() <= rich.starting_capital


def test_maximum_simultaneous_positions_is_respected():
    one_at_a_time = BacktestConfig(**{**CONFIG.__dict__, "starting_capital": 5_000_000.0, "max_allocation_pct": 20.0, "max_positions": 1})
    first = make_days(PROFITABLE)
    second = make_days(PROFITABLE, symbol="C2")
    result = run(pd.concat([first, second], ignore_index=True), one_at_a_time)
    assert result.equity["open_positions"].max() == 1 and result.summary["trades_executed"] == 1


# ---- no look-ahead -------------------------------------------------------------- #


def test_changing_future_prices_does_not_change_past_decisions():
    cutoff = START + pd.Timedelta(days=2)
    original = run(PROFITABLE)
    # Rewrite everything after the cutoff: a crash to expiry.
    shuffled = run(PROFITABLE[:3] + [(10, 15_000.0, 14_000.0), (30, 12_000.0, 12_000.0)])
    entry_columns = ["contract_symbol", "entry_date", "lots", "entry_spot", "entry_futures", "capital_used", "quoted_yield_pct"]
    pd.testing.assert_frame_equal(original.trades[entry_columns], shuffled.trades[entry_columns])
    early_equity = [result.equity.loc[result.equity["date"] <= cutoff, ["date", "equity", "capital_deployed"]].reset_index(drop=True) for result in (original, shuffled)]
    pd.testing.assert_frame_equal(*early_equity)


def test_liquidity_filter_skips_untraded_contracts_and_caps_size():
    assert run(make_days(PROFITABLE, volume=0.0)).summary["independent_opportunities"] == 0  # zero volume: not an opportunity
    thin = run(make_days(PROFITABLE, volume=20.0))  # 10% of 20 MT = 2 MT, less than one 3 MT lot
    assert thin.summary["trades_executed"] == 0 and "liquidity cap below one lot" in set(thin.opportunities["skipped"])


# ---- losing trades and empty periods ---------------------------------------------- #


def test_early_exit_loses_money_when_the_spread_widens_against_the_trade():
    config = BacktestConfig(**{**CONFIG.__dict__, "exit_mode": backtest.EXIT_EARLY, "exit_days_before": 5})
    # Entered with futures 400 over spot; five days before expiry futures are 700 over spot.
    trade = run(PROFITABLE[:-1] + [(25, 20_000.0, 20_700.0), (30, 20_300.0, 20_300.0)], config).trades.iloc[0]
    assert trade["gross_pnl"] == pytest.approx((20_400 - 20_700 + 20_000 - 20_000) * 30)  # -9,000
    assert trade["net_pnl"] < trade["gross_pnl"] < 0
    result = run(PROFITABLE[:-1] + [(25, 20_000.0, 20_700.0), (30, 20_300.0, 20_300.0)], config)
    assert result.summary["losses"] == 1 and result.summary["wins"] == 0
    assert result.summary["max_drawdown_rs"] > 0


def test_costs_can_exceed_the_spread_on_a_delivered_trade():
    # Rs 150 per quintal of transport turns a thin spread into a loss even though the signal fired.
    costly = BacktestConfig(**{**CONFIG.__dict__, "threshold_pct": 8.0, "transport_rs_per_quintal": 150.0})
    trade = run([(0, 20_000.0, 20_240.0), (30, 20_100.0, 20_100.0)], costly).trades.iloc[0]
    assert trade["gross_pnl"] == pytest.approx(240 * 30)
    assert trade["total_costs"] > trade["gross_pnl"] and trade["net_pnl"] < 0


def test_a_period_with_no_opportunities_does_nothing():
    flat = run([(day, 20_000.0, 20_020.0) for day in range(0, 31, 5)])  # futures barely above spot
    assert flat.trades.empty and flat.opportunities.empty
    assert flat.summary["trades_executed"] == 0 and flat.summary["total_net_profit"] == 0
    assert (flat.equity["equity"] == CONFIG.starting_capital).all()
    assert flat.summary["max_drawdown_rs"] == 0 and flat.summary["capital_utilisation_pct"] == 0
    assert np.isnan(flat.summary["avg_quoted_yield_pct"])


# ---- reverse arb uses inventory, not cash ------------------------------------------- #


def test_reverse_arb_is_limited_by_inventory_and_returns_it_at_expiry():
    reverse_only = BacktestConfig(**{**CONFIG.__dict__, "sides": (SIGNAL_REVERSE,), "inventory_lots": 2})
    # Futures 300 below spot with 30 days left.
    result = run([(0, 20_000.0, 19_700.0), (1, 20_000.0, 19_720.0), (30, 19_900.0, 19_900.0)], reverse_only)
    trade = result.trades.iloc[0]
    assert trade["side"] == SIGNAL_REVERSE and trade["lots"] == 2  # all the stock, no more
    assert trade["capital_used"] == pytest.approx(20_000 * 60)  # inventory value released, not cash
    # Sold spot at 20,000, bought futures at 19,700, took delivery at 19,900: 300 per quintal x 60.
    assert trade["gross_pnl"] == pytest.approx(300 * 60)
    assert trade["saved_storage"] == pytest.approx(1.0 * 30 * 60)  # 30 days of rent not paid
    assert trade["cost_stamp_duty"] == pytest.approx(19_700 * 0.002 / 100 * 60)  # futures were bought
    assert (result.equity["capital_deployed"] == 0).all()  # no cash is tied up
    assert result.equity["inventory_released"].max() == pytest.approx(1_200_000.0)
