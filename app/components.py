"""Shared building blocks: KPI cards, the signal badge, and the chart wrapper."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import theme
from formatting import DAGGER
from src.metrics import SIGNAL_CASH_AND_CARRY, SIGNAL_REVERSE

SHORT_DATED_NOTE = "short-dated, annualisation exaggerates"

# Label, colour and a shape, so the signal never depends on colour alone.
SIGNAL_STYLE = {
    SIGNAL_CASH_AND_CARRY: ("Cash-and-carry", theme.CARRY, "▲"),
    SIGNAL_REVERSE: ("Reverse arb", theme.REVERSE, "▼"),
    "NONE": ("No trade", theme.MUTED, "■"),
}

CSS = f"""
<style>
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }}
.kpi {{ border: 1px solid {theme.GRID}; border-radius: 8px; padding: 10px 12px; background: white; min-height: 132px; margin-bottom: 14px; }}
.kpi-label {{ font-size: 0.74rem; color: {theme.MUTED}; text-transform: uppercase; letter-spacing: 0.04em; }}
.kpi-value {{ font-size: 1.28rem; font-weight: 600; color: {theme.INK}; line-height: 1.5; white-space: nowrap; }}
.kpi-value.greyed {{ color: {theme.MUTED}; font-weight: 500; }}
.kpi-sub {{ font-size: 0.78rem; color: {theme.MUTED}; line-height: 1.35; }}
.badge {{ display: inline-block; padding: 1px 10px; border-radius: 999px; color: white; font-size: 0.95rem; font-weight: 600; }}
.question {{ font-size: 1.05rem; font-weight: 600; color: {theme.INK}; margin: 0.9rem 0 0 0; }}
.takeaway {{ font-size: 0.9rem; color: {theme.SPOT}; margin: 0 0 1.1rem 0; border-left: 3px solid {theme.FUTURES}; padding-left: 10px; }}
.flow {{ display: flex; align-items: stretch; gap: 6px; margin: 0.6rem 0 1rem 0; }}
.flow-step {{ flex: 1; border: 1px solid {theme.FAINT}; border-radius: 8px; padding: 10px 12px; background: white; font-size: 0.86rem; }}
.flow-step b {{ display: block; color: {theme.INK}; }}
.flow-arrow {{ align-self: center; color: {theme.MUTED}; font-size: 1.2rem; }}
.kpi-sub .greyed, .greyed {{ color: {theme.TBILL}; }}
.unit {{ font-size: 0.8rem; font-weight: 400; color: {theme.MUTED}; }}
.lede {{ font-size: 1.02rem; color: {theme.INK}; margin: 0.4rem 0 0.9rem 0; max-width: 1050px; }}
.finding-title {{ font-size: 1.0rem; font-weight: 600; margin: 0; min-height: 3em; }}
.finding-body {{ font-size: 0.87rem; color: {theme.SPOT}; margin: 0.25rem 0 0 0; min-height: 5.6em; }}
.insight {{ font-size: 0.93rem; color: {theme.INK}; margin: 0.15rem 0; }}
.legend-note {{ font-size: 0.8rem; color: {theme.MUTED}; }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def kpi(label: str, value: str, sub: str = "", greyed: bool = False) -> None:
    """A small card: label, headline value, one line of context. `value` may contain HTML."""
    value_class = "kpi-value greyed" if greyed else "kpi-value"
    st.markdown(
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="{value_class}">{value}</div><div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )


def kpi_row(cards: list[dict], widths: list[float] | None = None) -> None:
    """A row of KPI cards; `widths` gives relative column widths (equal by default)."""
    for column, card in zip(st.columns(widths or len(cards)), cards):
        with column:
            kpi(**card)


def signal_badge(signal: str) -> str:
    label, colour, shape = SIGNAL_STYLE[signal]
    return f'<span class="badge" style="background:{colour}">{shape} {label}</span>'


def chart(figure: go.Figure, question: str, takeaway: str, key: str, height: int = 360) -> None:
    """Draw a chart under the question it answers, with a one-line takeaway from the data."""
    st.markdown(f'<p class="question">{question}</p>', unsafe_allow_html=True)
    figure.update_layout(height=height)
    st.plotly_chart(figure, key=key, config={"displayModeBar": False})
    st.markdown(f'<p class="takeaway">{takeaway}</p>', unsafe_allow_html=True)


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


def shade_runs(figure: go.Figure, dates: pd.Series, mask: pd.Series, colour: str, opacity: float = 0.12) -> None:
    """Shade the background over each unbroken run of days where `mask` is true."""
    dates, mask = dates.reset_index(drop=True), mask.reset_index(drop=True)
    run_id = (mask != mask.shift()).cumsum()
    for _, run in dates[mask].groupby(run_id[mask]):
        figure.add_vrect(x0=run.iloc[0], x1=run.iloc[-1] + pd.Timedelta(days=1), fillcolor=colour, opacity=opacity, line_width=0, layer="below")
