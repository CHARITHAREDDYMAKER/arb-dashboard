"""Colours and the shared Plotly template. Every chart in the app uses these."""

import plotly.graph_objects as go
import plotly.io as pio

# One meaning per colour, used everywhere.
SPOT = "#3b3a37"  # dark grey
FUTURES = "#2a78d6"  # blue
CARRY = "#1baf7a"  # teal: cash-and-carry
REVERSE = "#eb6834"  # orange: reverse arb
TBILL = "#8a8983"  # grey, always drawn dashed

INK = "#0b0b0b"
MUTED = "#6f6e69"
FAINT = "#c9c8c2"  # short-dated or secondary marks
GRID = "#e8e7e2"
SURFACE = "#fcfcfb"

FONT = "Source Sans Pro, Segoe UI, Helvetica, Arial, sans-serif"
TEMPLATE_NAME = "arb"


def register_template() -> None:
    """Register the app's Plotly template and make it the default."""
    axis = dict(gridcolor=GRID, linecolor=FAINT, zerolinecolor=FAINT, tickfont=dict(color=MUTED, size=12), title_font=dict(color=MUTED, size=12))
    pio.templates[TEMPLATE_NAME] = go.layout.Template(
        layout=go.Layout(
            font=dict(family=FONT, color=INK, size=13),
            paper_bgcolor=SURFACE,
            plot_bgcolor=SURFACE,
            colorway=[FUTURES, CARRY, REVERSE, SPOT],
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis={**axis, "showgrid": False},
            yaxis=axis,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=12, color=INK)),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="white", font=dict(family=FONT, size=12)),
            bargap=0.25,
        )
    )
    pio.templates.default = TEMPLATE_NAME


def hex_to_rgba(colour: str, alpha: float) -> str:
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{alpha})"
