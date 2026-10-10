"""Number formatting: Indian digit grouping, rupees, lakh/crore and percentages."""

import math

DAGGER = "†"  # marks a figure that depends on an assumption (handling, GST on storage)


def indian_number(value: float, decimals: int = 2) -> str:
    """12345678.9 -> '1,23,45,678.90' (last three digits, then groups of two)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    sign = "-" if value < 0 else ""
    whole, _, fraction = f"{abs(value):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return f"{sign}{whole}" + (f".{fraction}" if decimals else "")


def rs(value: float, decimals: int = 2, signed: bool = False) -> str:
    """Rupees: 'Rs 24,626.00'; with signed=True a plus sign is shown for gains."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    sign = "+" if signed and value > 0 else ("-" if value < 0 else "")
    return f"{sign}Rs {indian_number(abs(value), decimals)}"


def rs_crore(value: float, decimals: int = 1) -> str:
    return "n/a" if value is None or math.isnan(value) else f"Rs {indian_number(value, decimals)} crore"


def rs_large(value: float) -> str:
    """Pick lakh or crore for a rupee amount."""
    if abs(value) >= 1e7:
        return f"Rs {indian_number(value / 1e7, 2)} crore"
    if abs(value) >= 1e5:
        return f"Rs {indian_number(value / 1e5, 2)} lakh"
    return rs(value, 0)


def pct(value: float, signed: bool = False, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:+.{decimals}f}%" if signed else f"{value:.{decimals}f}%"


def long_date(timestamp) -> str:
    return f"{timestamp.day} {timestamp:%b %Y}"
