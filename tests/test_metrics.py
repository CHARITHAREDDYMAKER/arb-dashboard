"""Unit tests for the metrics engine, using small examples worked out by hand.

Run with:  python -m pytest
"""

import numpy as np
import pandas as pd
import pytest

from src import metrics
from src.metrics import CostInputs

# Jeera-like inputs with round numbers that are easy to check on paper.
COSTS = CostInputs(
    storage_rs_per_quintal_per_day=0.70,
    assaying_rs_per_quintal=50.0,
    handling_rs_per_quintal=10.0,
    brokerage_rs_per_lakh=10.0,
    funding_spread_pct=1.0,
    exchange_fee_rs_per_lakh=5.8,
    sebi_fee_rs_per_crore=1.0,
    stamp_duty_pct=0.002,
    gst_pct=18.0,
    gst_on_storage_and_assaying=True,
)
SPOT, FUTURES, DAYS, TBILL = 20_000.0, 20_400.0, 73, 5.0  # 73 days = exactly one fifth of a year


# ---- annualisation -------------------------------------------------------- #


def test_annualise_scales_to_365_days():
    assert metrics.annualise(1.0, 73) == pytest.approx(5.0)
    assert metrics.annualise(2.0, 365) == pytest.approx(2.0)


def test_annualise_is_undefined_on_expiry_day():
    assert np.isnan(metrics.annualise(1.0, 0))


# ---- cost model ----------------------------------------------------------- #


def test_funding_cost_uses_tbill_plus_spread():
    # 20,000 x (5% + 1%) x 73/365 = 20,000 x 0.06 x 0.2
    assert metrics.funding_cost(SPOT, TBILL, DAYS, COSTS) == pytest.approx(240.0)


def test_storage_cost_includes_gst_only_when_switched_on():
    # 0.70 x 73 days = 51.10, plus 18% GST = 60.298
    assert metrics.storage_cost(DAYS, COSTS) == pytest.approx(60.298)
    without_gst = CostInputs(**{**COSTS.__dict__, "gst_on_storage_and_assaying": False})
    assert metrics.storage_cost(DAYS, without_gst) == pytest.approx(51.10)


def test_futures_leg_costs_sell_side():
    leg = metrics.futures_leg_costs(FUTURES, COSTS, buys_futures=False)
    assert leg["exchange_fee"] == pytest.approx(1.1832)  # 20,400 x 5.8 / 1,00,000
    assert leg["sebi_fee"] == pytest.approx(0.00204)  # 20,400 x 1 / 1,00,00,000
    assert leg["brokerage"] == pytest.approx(2.04)  # 20,400 x 10 / 1,00,000
    assert leg["gst_on_fees"] == pytest.approx(0.580176)  # 18% of (1.1832 + 2.04)
    assert leg["stamp_duty"] == 0  # seller pays no stamp duty
    assert leg["ctt"] == 0  # exempt agricultural commodity


def test_stamp_duty_is_charged_on_the_buy_side_only():
    leg = metrics.futures_leg_costs(FUTURES, COSTS, buys_futures=True)
    assert leg["stamp_duty"] == pytest.approx(0.408)  # 20,400 x 0.002%


def test_cash_and_carry_adds_up():
    result = metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, COSTS)
    transaction = 1.1832 + 0.00204 + 2.04 + 0.580176  # 3.805416
    non_funding = 60.298 + 59.0 + 10.0 + transaction  # storage, assaying (50 x 1.18), handling
    assert result["cc_gross"] == pytest.approx(400.0)
    assert result["cc_assaying"] == pytest.approx(59.0)
    assert result["cc_transaction"] == pytest.approx(transaction)
    assert result["cc_net_profit"] == pytest.approx(400.0 - 240.0 - non_funding)  # 26.896584
    # Return before funding: (400 - 133.103416) / 20,000 = 1.3345%, times 5 to annualise.
    assert result["cc_carry_return_pct"] == pytest.approx((400.0 - non_funding) / SPOT * 100 * 5)
    assert result["cc_excess_over_tbill"] == pytest.approx(result["cc_carry_return_pct"] - TBILL)


def test_profit_is_positive_exactly_when_return_beats_the_funding_rate():
    result = metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, COSTS)
    funding_rate = TBILL + COSTS.funding_spread_pct
    assert (result["cc_net_profit"] > 0) == (result["cc_carry_return_pct"] > funding_rate)


def test_break_even_storage_makes_net_profit_zero():
    break_even = metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, COSTS)["break_even_storage"]
    at_break_even = CostInputs(**{**COSTS.__dict__, "storage_rs_per_quintal_per_day": float(break_even)})
    assert metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, at_break_even)["cc_net_profit"] == pytest.approx(0.0, abs=1e-9)
    assert break_even > COSTS.storage_rs_per_quintal_per_day  # the trade is profitable, so there is room


def test_reverse_arb_when_futures_are_below_spot():
    futures = 19_800.0
    result = metrics.reverse_arb(SPOT, futures, DAYS, TBILL, COSTS)
    # Buy-side transaction costs on 19,800: fee 1.1484, SEBI 0.00198, stamp 0.396, brokerage 1.98, GST 0.563112
    transaction = 1.1484 + 0.00198 + 0.396 + 1.98 + 0.563112
    assert result["rev_price_gap"] == pytest.approx(200.0)
    assert result["rev_avoided_carry"] == pytest.approx(240.0 + 60.298)
    assert result["rev_benefit"] == pytest.approx(200.0 + 300.298 - transaction)
    assert result["rev_return_pct"] == pytest.approx(result["rev_benefit"] / SPOT * 100 * 5)


# ---- fair value and signal ------------------------------------------------ #


def test_fair_value_is_spot_plus_funding_and_storage():
    assert metrics.fair_value(SPOT, DAYS, TBILL, COSTS) == pytest.approx(20_000 + 240.0 + 60.298)


def test_signal_thresholds():
    signals = metrics.classify_signal([0.30, 0.25, 0.0, -0.25, -0.30], threshold_pct=0.25)
    assert list(signals) == ["CASH_AND_CARRY", "NONE", "NONE", "NONE", "REVERSE"]


def test_tradeable_signal_needs_profit_after_all_costs():
    signals = ["CASH_AND_CARRY", "CASH_AND_CARRY", "REVERSE", "REVERSE", "NONE"]
    carry_profit = [25.0, -5.0, -400.0, -400.0, 30.0]
    reverse_benefit = [-300.0, -300.0, 40.0, 0.0, 10.0]
    tradeable = metrics.classify_tradeable(signals, carry_profit, reverse_benefit)
    # Kept only when its own side makes money; a NONE signal is never upgraded.
    assert list(tradeable) == ["CASH_AND_CARRY", "NONE", "REVERSE", "NONE", "NONE"]


def test_small_mispricing_can_signal_but_not_be_tradeable():
    # Futures Rs 60 (0.30%) above fair value over 10 days: a raw cash-and-carry signal,
    # but the one-off assaying (59) and handling (10) cost more than the edge.
    spot, days = 20_000.0, 10
    futures = metrics.fair_value(spot, days, TBILL, COSTS) + 60.0
    result = metrics.cash_and_carry(spot, futures, days, TBILL, COSTS)
    signal = metrics.classify_signal([60.0 / spot * 100], threshold_pct=0.25)
    assert list(signal) == ["CASH_AND_CARRY"]
    assert result["cc_net_profit"] < 0
    assert list(metrics.classify_tradeable(signal, [result["cc_net_profit"]], [0.0])) == ["NONE"]


def test_waterfall_lines_add_up_to_net_profit():
    result = metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, COSTS)
    storage_ex_gst, assaying_ex_gst = 51.10, 50.0
    lines = (
        result["cc_funding"] + storage_ex_gst + assaying_ex_gst + result["cc_handling"]
        + result["cc_fees_ex_gst"] + result["cc_gst"]
    )  # fmt: skip
    assert result["cc_gross"] - lines == pytest.approx(result["cc_net_profit"])


# ---- roll rule ------------------------------------------------------------ #


def make_futures(day: str, days_to_expiry: list[int]) -> pd.DataFrame:
    """One day's contracts for one commodity, ranked by expiry."""
    date = pd.Timestamp(day)
    return pd.DataFrame(
        {
            "date": date,
            "commodity": "JEERA",
            "contract_symbol": [f"C{rank}" for rank in range(1, len(days_to_expiry) + 1)],
            "expiry": [date + pd.Timedelta(days=days) for days in days_to_expiry],
            "days_to_expiry": days_to_expiry,
            "contract_rank": range(1, len(days_to_expiry) + 1),
        }
    )


def active_symbol(futures: pd.DataFrame, threshold: int = 5) -> list[str]:
    marked = metrics.mark_active_contract(futures, threshold)
    return marked.loc[marked["is_active"], "contract_symbol"].tolist()


def test_near_month_is_active_until_five_days_before_expiry():
    assert active_symbol(make_futures("2026-01-05", [15, 45, 75])) == ["C1"]
    assert active_symbol(make_futures("2026-01-15", [5, 35, 65])) == ["C1"]  # exactly 5 days: still near


def test_rolls_to_next_month_inside_five_days():
    assert active_symbol(make_futures("2026-01-16", [4, 34, 64])) == ["C2"]
    assert active_symbol(make_futures("2026-01-20", [0, 30, 60])) == ["C2"]  # expiry day


def test_roll_handles_a_missing_month():
    # Jeera has no February contract: after January, the March contract is next and about 60 days out.
    assert active_symbol(make_futures("2026-01-18", [2, 61, 92])) == ["C2"]
    assert active_symbol(make_futures("2026-01-21", [58, 89])) == ["C1"]


def test_exactly_one_active_contract_per_commodity_per_day():
    both_days = pd.concat([make_futures("2026-01-05", [15, 45]), make_futures("2026-01-18", [2, 32])])
    other = both_days.assign(commodity="GUARSEED")
    marked = metrics.mark_active_contract(pd.concat([both_days, other]), 5)
    assert (marked.groupby(["date", "commodity"])["is_active"].sum() == 1).all()


# ---- basis convention -------------------------------------------------------- #


def test_basis_is_spot_minus_futures():
    day = pd.Timestamp("2026-01-05")
    futures = make_futures("2026-01-05", [30, 60]).assign(settlement_price=[20_400.0, 19_800.0], spot=20_000.0, open_interest=[100, 100])
    spot = pd.DataFrame({"date": [day], "commodity": ["JEERA"], "spot": [20_000.0]})
    table = metrics.basis_table(futures, spot, metrics.Rules()).set_index("contract_symbol")
    # Futures above spot: negative basis. Futures below spot: positive basis.
    assert table.loc["C1", "basis"] == pytest.approx(-400.0)
    assert table.loc["C1", "basis_pct"] == pytest.approx(-2.0)
    assert table.loc["C2", "basis"] == pytest.approx(200.0)
    assert table.loc["C2", "basis_pct"] == pytest.approx(1.0)
    # The futures premium (used for the curve and for cash-and-carry) is the basis with its sign flipped.
    assert table.loc["C1", "futures_premium_pct"] == pytest.approx(2.0)
    assert metrics.cash_and_carry(20_000.0, 20_400.0, 30, TBILL, COSTS)["cc_gross"] == pytest.approx(-table.loc["C1", "basis"])
    assert metrics.reverse_arb(20_000.0, 19_800.0, 60, TBILL, COSTS)["rev_price_gap"] == pytest.approx(table.loc["C2", "basis"])


# ---- three-day average spot ----------------------------------------------- #


def test_three_day_spot_average():
    spot = pd.DataFrame(
        {"date": pd.date_range("2026-01-05", periods=4), "commodity": "JEERA", "spot": [100.0, 110.0, 120.0, 150.0]}
    )
    averaged = metrics.add_three_day_spot(spot)["spot_3d_avg"]
    assert np.isnan(averaged.iloc[1])  # not enough history yet
    assert averaged.iloc[2] == pytest.approx(110.0)
    assert averaged.iloc[3] == pytest.approx(380.0 / 3)


# ---- entry timing --------------------------------------------------------- #


def test_fixed_costs_hurt_short_trades_more():
    # Same Rs 60 edge over fair value, entered 10 days and 60 days before expiry.
    short = metrics.cash_and_carry(SPOT, metrics.fair_value(SPOT, 10, TBILL, COSTS) + 60, 10, TBILL, COSTS)
    long = metrics.cash_and_carry(SPOT, metrics.fair_value(SPOT, 60, TBILL, COSTS) + 60, 60, TBILL, COSTS)
    # Net profit is almost the same in rupees (both carry the Rs 69 one-off cost)...
    assert short["cc_net_profit"] == pytest.approx(long["cc_net_profit"], abs=0.5)
    # ...but per day of holding the short trade loses six times as fast.
    assert short["cc_net_profit"] / 10 < long["cc_net_profit"] / 60 < 0


# ---- wording of short-dated trades ----------------------------------------- #


def trade_day(days: int, signal: str) -> pd.Series:
    return pd.Series(
        {
            "tradeable_signal": signal, "days_to_expiry": days, "tbill_pct": 5.5,
            "cc_net_profit": 78.0, "cc_net_profit_pct": 0.32, "cc_carry_return_pct": 18.1,
            "rev_benefit": 228.0, "rev_benefit_pct": 2.95, "rev_return_pct": 154.0,
        }
    )  # fmt: skip


def test_short_dated_trade_leads_with_rupees_and_holding_period_return():
    text = metrics.describe_trade(trade_day(10, "CASH_AND_CARRY"))
    assert text.startswith("cash-and-carry locks in Rs 78 per quintal, 0.32% over 10 days")
    assert "18% annualised, which exaggerates" in text


def test_longer_dated_trade_leads_with_the_annualised_return():
    text = metrics.describe_trade(trade_day(40, "REVERSE"))
    assert text == "a stock holder gains 154.0% annualised by selling spot and buying futures"


def test_no_tradeable_signal_says_so():
    assert metrics.describe_trade(trade_day(10, "NONE")) == "neither side makes money after all costs"


# ---- signal finder ---------------------------------------------------------- #


def amounts(lines: list[tuple]) -> dict:
    return {label: amount for label, _, amount in lines}


def test_calculator_carry_lines_match_the_cost_model_exactly():
    calc = metrics.trade_calculator(SPOT, FUTURES, DAYS, TBILL, COSTS)
    model = metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, COSTS)
    lines = amounts(calc["carry"])
    assert lines["Futures premium"] == model["cc_gross"]
    assert lines["Storage"] == -model["cc_storage"]
    assert lines["Assaying"] == -model["cc_assaying"]
    assert lines["Handling"] == -model["cc_handling"]
    assert lines["Funding"] == -model["cc_funding"]
    assert lines["= Carry profit before funding"] == model["cc_carry_profit"]
    assert lines["= Net profit after funding"] == model["cc_net_profit"]
    assert calc["summary"]["carry_annualised_pct"] == model["cc_carry_return_pct"]
    assert calc["summary"]["carry_excess_over_tbill"] == model["cc_excess_over_tbill"]


def test_calculator_carry_lines_add_up():
    calc = metrics.trade_calculator(SPOT, FUTURES, DAYS, TBILL, COSTS)
    lines = calc["carry"]
    before_funding = sum(amount for label, _, amount in lines if not label.startswith("=") and label != "Funding")
    assert before_funding == pytest.approx(calc["summary"]["carry_profit"])
    assert before_funding + amounts(lines)["Funding"] == pytest.approx(calc["summary"]["carry_net_profit"])
    # holding-period return x 365 / days = annualised return
    assert calc["summary"]["carry_holding_pct"] * 365 / DAYS == pytest.approx(calc["summary"]["carry_annualised_pct"])


def test_calculator_reverse_lines_match_and_add_up():
    futures = 19_800.0
    calc = metrics.trade_calculator(SPOT, futures, DAYS, TBILL, COSTS)
    model = metrics.reverse_arb(SPOT, futures, DAYS, TBILL, COSTS)
    lines = amounts(calc["reverse"])
    assert lines["Basis"] == model["rev_price_gap"]
    assert lines["Avoided funding"] + lines["Avoided storage"] == pytest.approx(model["rev_avoided_carry"])
    assert lines["Stamp duty"] == pytest.approx(-0.396)  # 19,800 x 0.002%, charged because futures are bought
    assert lines["= Net benefit"] == model["rev_benefit"]
    assert sum(amount for label, amount in lines.items() if not label.startswith("=")) == pytest.approx(model["rev_benefit"])
    assert calc["summary"]["reverse_annualised_pct"] == model["rev_return_pct"]
    assert calc["summary"]["reverse_ex_funding_annualised_pct"] == model["rev_return_ex_funding_pct"]
    assert calc["summary"]["reverse_excess_over_tbill"] == model["rev_excess_over_tbill"]


def test_reverse_arb_is_compared_with_the_tbill_excluding_avoided_funding():
    futures = 19_800.0
    result = metrics.reverse_arb(SPOT, futures, DAYS, TBILL, COSTS)
    # Benefit 496.2085 includes Rs 240 of avoided funding (20,000 x 6% x 73/365).
    assert result["rev_avoided_funding"] == pytest.approx(240.0)
    assert result["rev_benefit_ex_funding"] == pytest.approx(result["rev_benefit"] - 240.0)
    # (benefit - 240) / 20,000 x 100, times 5 to annualise
    assert result["rev_return_ex_funding_pct"] == pytest.approx((result["rev_benefit"] - 240.0) / SPOT * 100 * 5)
    assert result["rev_excess_over_tbill"] == pytest.approx(result["rev_return_ex_funding_pct"] - TBILL)
    # The gap between the two returns is exactly the funding rate (T-bill 5% + spread 1%).
    assert result["rev_return_pct"] - result["rev_return_ex_funding_pct"] == pytest.approx(TBILL + COSTS.funding_spread_pct)


def test_signal_days_measure_the_tbill_gap_from_the_ex_funding_return():
    economics = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06"]), "commodity": "JEERA",
            "tradeable_signal": ["CASH_AND_CARRY", "REVERSE"], "contract_symbol": ["C1", "C1"], "days_to_expiry": [30, 29],
            "spot": [20_000.0, 20_000.0], "settlement_price": [20_400.0, 19_800.0], "basis_pct": [2.0, -1.0], "tbill_pct": [5.0, 5.0],
            "cc_net_profit": [100.0, -500.0], "rev_benefit": [-300.0, 250.0],
            "cc_holding_return_pct": [1.0, -2.0], "rev_benefit_pct": [-1.5, 1.25],
            "cc_carry_return_pct": [12.0, -24.0], "rev_return_pct": [-18.0, 15.0], "rev_return_ex_funding_pct": [-24.0, 9.0],
        }
    )  # fmt: skip
    settlements = pd.DataFrame(columns=["contract_symbol", "final_settlement", "convergence_error_pct"])
    days = metrics.signal_days(economics, settlements).set_index("side")
    assert days.loc["CASH_AND_CARRY", "excess_over_tbill"] == pytest.approx(12.0 - 5.0)  # already before funding
    assert days.loc["REVERSE", "ex_funding_annualised_pct"] == pytest.approx(9.0)
    assert days.loc["REVERSE", "excess_over_tbill"] == pytest.approx(9.0 - 5.0)  # not 15 - 5


def make_signal_days() -> pd.DataFrame:
    rows = [
        # date, side, days to expiry, net Rs, annualised %
        ("2026-01-05", "CASH_AND_CARRY", 40, 100.0, 12.0),
        ("2026-01-06", "CASH_AND_CARRY", 39, 300.0, 20.0),
        ("2026-01-07", "CASH_AND_CARRY", 8, 150.0, 90.0),
        ("2026-01-08", "REVERSE", 30, 250.0, 25.0),
        ("2026-01-09", "CASH_AND_CARRY", 20, 50.0, 30.0),
    ]
    frame = pd.DataFrame(rows, columns=["date", "side", "days_to_expiry", "net_rs", "annualised_pct"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["short_dated"] = frame["days_to_expiry"] < metrics.SHORT_DATED_DAYS
    return frame


def test_main_ranking_excludes_short_dated_days():
    best = metrics.best_dates(make_signal_days(), "CASH_AND_CARRY", metrics.RANK_BY_ANNUALISED)
    assert list(best["annualised_pct"]) == [30.0, 20.0, 12.0]  # the 90% day has 8 days to run and is left out


def test_rupee_ranking_uses_every_day_and_short_dated_ranking_only_short_ones():
    days = make_signal_days()
    assert list(metrics.best_dates(days, "CASH_AND_CARRY", metrics.RANK_BY_RUPEES)["net_rs"]) == [300.0, 150.0, 100.0, 50.0]
    assert list(metrics.best_dates(days, "CASH_AND_CARRY", metrics.RANK_SHORT_DATED)["annualised_pct"]) == [90.0]
    assert list(metrics.best_dates(days, "REVERSE", metrics.RANK_BY_ANNUALISED)["net_rs"]) == [250.0]


# ---- implied carry and funding labels --------------------------------------- #


def test_implied_carry_is_log_of_futures_over_spot_annualised():
    # ln(20,400 / 20,000) = 0.019803; x 365 / 73 = x 5; x 100 = 9.9013%
    assert metrics.implied_carry(SPOT, FUTURES, DAYS) == pytest.approx(9.9013, abs=1e-4)
    assert np.isnan(metrics.implied_carry(SPOT, FUTURES, 0))


def test_cost_of_carry_is_funding_plus_storage_annualised():
    # funding 240 + storage 60.298 = 300.298; / 20,000 = 1.50149%; x 5 = 7.50745%
    assert metrics.cost_of_carry(SPOT, DAYS, TBILL, COSTS) == pytest.approx(7.50745)


def test_before_and_after_funding_returns_differ_by_the_funding_rate():
    result = metrics.cash_and_carry(SPOT, FUTURES, DAYS, TBILL, COSTS)
    before, after = result["cc_carry_return_pct"], result["cc_return_after_funding_pct"]
    assert before - after == pytest.approx(TBILL + COSTS.funding_spread_pct)  # 6 points
    assert after == pytest.approx(result["cc_net_profit"] / SPOT * 100 * 5)
