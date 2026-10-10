"""Shared building blocks: KPI cards, sparklines, the signal badge, and the chart wrapper."""

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import theme
from formatting import DAGGER
from src.metrics import SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE

SHORT_DATED_NOTE = "short-dated, annualisation exaggerates"


def signal_style(signal: str) -> tuple[str, str, str]:
    """Label, colour and a shape for a signal, so it never depends on colour alone."""
    return {
        SIGNAL_CASH_AND_CARRY: ("Cash-and-carry", theme.CARRY, "▲"),
        SIGNAL_REVERSE: ("Reverse arb", theme.REVERSE, "▼"),
    }.get(signal, ("No trade", theme.TBILL, "■"))


def signal_badge(signal: str) -> str:
    label, colour, shape = signal_style(signal)
    return f'<span class="badge" style="background:{colour}; --glow:{theme.hex_to_rgba(colour, 0.55)}">{shape} {label}</span>'


def sparkline(values, colour: str | None = None) -> str:
    """A small inline SVG line of recent values (no axes: it shows direction, not levels)."""
    values = [float(value) for value in values if value is not None and not math.isnan(float(value))]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    spread = (high - low) or 1.0
    step = 100 / (len(values) - 1)
    points = " ".join(f"{index * step:.1f},{22 - (value - low) / spread * 20:.1f}" for index, value in enumerate(values))
    colour = colour or theme.FUTURES
    return (
        f'<svg viewBox="0 0 100 24" preserveAspectRatio="none" role="img" aria-label="last {len(values)} trading days">'
        f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="1.6" vector-effect="non-scaling-stroke" '
        'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def delta(change: float | None, text: str) -> str:
    """Change against the previous trading day: an arrow and a colour, never colour alone."""
    if change is None or math.isnan(change):
        return ""
    if abs(change) < 1e-9:
        return f'<span class="delta-flat">● {text}</span>'
    arrow, css_class = ("▲", "delta-up") if change > 0 else ("▼", "delta-down")
    return f'<span class="{css_class}">{arrow} {text}</span>'


def kpi(label: str, value: str, sub: str = "", greyed: bool = False, spark: str = "") -> None:
    """A card: label, headline value, one line of context, and an optional sparkline. `value` may contain HTML."""
    value_class = "kpi-value greyed" if greyed else "kpi-value"
    st.markdown(
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="{value_class}">{value}</div><div class="kpi-sub">{sub}</div>{spark}</div>',
        unsafe_allow_html=True,
    )


def kpi_row(cards: list[dict], widths: list[float] | None = None) -> None:
    """A row of KPI cards; `widths` gives relative column widths (equal by default)."""
    for column, card in zip(st.columns(widths or len(cards)), cards):
        with column:
            kpi(**card)


def chart(figure: go.Figure, question: str, takeaway: str, key: str, height: int = 360, **plotly_arguments):
    """Draw a chart under the question it answers, with a one-line takeaway from the data."""
    st.markdown(f'<p class="question">{question}</p>', unsafe_allow_html=True)
    figure.update_layout(height=height)
    event = st.plotly_chart(figure, key=key, config={"displayModeBar": False}, **plotly_arguments)
    st.markdown(f'<p class="takeaway">{takeaway}</p>', unsafe_allow_html=True)
    return event


def has_rows(table: pd.DataFrame, missing: str) -> bool:
    """True if `table` has data. Otherwise show a note saying what the date range lacks.

    A narrow date range can leave a chart with nothing to draw (for example a
    week with no contract expiry). The tab explains that and carries on.
    """
    if table.empty:
        st.info(f"{missing} Widen the date range in the sidebar to see this section.")
        return False
    return True


def dagger_legend() -> None:
    st.markdown(
        f'<p class="legend-note">{DAGGER} depends on an assumption: handling cost (not published) or 18% GST on storage and '
        "assaying (treatment not verified). Both can be changed under Desk inputs.</p>",
        unsafe_allow_html=True,
    )


def add_expiry_lines(figure: go.Figure, expiries, row: int | None = None) -> None:
    """Thin dotted vertical lines at contract expiries."""
    for expiry in expiries:
        figure.add_vline(x=expiry, line_width=1, line_dash="dot", line_color=theme.FAINT, row=row, col=1 if row else None)


def shade_runs(figure: go.Figure, dates: pd.Series, mask: pd.Series, colour: str, opacity: float = 0.14) -> None:
    """Shade the background over each unbroken run of days where `mask` is true."""
    dates, mask = dates.reset_index(drop=True), mask.reset_index(drop=True)
    run_id = (mask != mask.shift()).cumsum()
    for _, run in dates[mask].groupby(run_id[mask]):
        figure.add_vrect(x0=run.iloc[0], x1=run.iloc[-1] + pd.Timedelta(days=1), fillcolor=colour, opacity=opacity, line_width=0, layer="below")
