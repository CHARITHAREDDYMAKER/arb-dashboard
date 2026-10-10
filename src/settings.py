"""Load the config files into the input objects the metrics functions take."""

import tomllib
from pathlib import Path

from src.metrics import QUINTALS_PER_MT, CostInputs, Rules

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def read_config(name: str) -> dict:
    return tomllib.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def load_commodities() -> dict:
    return read_config("commodities.toml")


def load_rules() -> Rules:
    assumptions = read_config("assumptions.toml")
    rules, capacity = assumptions["rules"], assumptions["capacity"]
    return Rules(
        roll_dte_threshold=rules["roll_dte_threshold"],
        min_oi=rules["min_oi"],
        signal_threshold_pct=rules["signal_threshold_pct"],
        capacity_share_of_volume=capacity["share_of_volume"],
        capacity_window_days=capacity["window_days"],
    )


def has_cold_storage(commodity: str) -> bool:
    return "cold_storage_value" in read_config("carry_costs.toml")["warehouse_storage"][commodity]


def load_cost_inputs(commodity: str, cold_storage: bool = False) -> CostInputs:
    """Default cost inputs for one commodity, converted to Rs per quintal.

    `cold_storage` swaps in the cold-storage rent where NCCL publishes one (jeera).
    """
    published = read_config("carry_costs.toml")
    assumed = read_config("assumptions.toml")["costs"]

    storage = published["warehouse_storage"][commodity]
    storage_per_mt_per_day = storage["cold_storage_value"] if cold_storage else storage["value"]
    assaying = published["assaying"][commodity]
    return CostInputs(
        storage_rs_per_quintal_per_day=storage_per_mt_per_day / QUINTALS_PER_MT,
        assaying_rs_per_quintal=assaying["value"] / (assaying["sample_lot_mt"] * QUINTALS_PER_MT),
        handling_rs_per_quintal=assumed["handling"]["rs_per_quintal"],
        brokerage_rs_per_lakh=assumed["brokerage_rs_per_lakh"],
        funding_spread_pct=assumed["funding_spread_pct"],
        exchange_fee_rs_per_lakh=published["exchange_transaction_fee"]["value"],
        sebi_fee_rs_per_crore=published["sebi_turnover_fee"]["value"],
        stamp_duty_pct=published["stamp_duty"]["value"],
        gst_pct=published["gst_on_brokerage_and_fees"]["value"],
        gst_on_storage_and_assaying=assumed["gst_on_storage_and_assaying"],
        ctt_pct=published["commodity_transaction_tax"]["value"],
    )
