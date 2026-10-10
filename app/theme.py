"""Colours, the shared Plotly template and the page CSS, for the dark and light themes.

`activate(mode)` is called once at the top of every run. It sets the module-level
colour names below (so chart code can write `theme.FUTURES`), registers the
Plotly template and injects the CSS.

One meaning per colour, in both themes:
    SPOT     neutral grey (light on dark, dark on light)
    FUTURES  blue
    CARRY    teal: cash-and-carry
    REVERSE  orange: reverse arb
    TBILL    grey, always drawn dashed
"""

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

DARK, LIGHT = "dark", "light"

PALETTES = {
    DARK: dict(
        SPOT="#d5dae6", FUTURES="#3987e5", CARRY="#199e70", REVERSE="#d95926", TBILL="#8590a6",
        INK="#eef1f7", MUTED="#97a1b5", FAINT="#3a4560", GRID="#1b2437",
        PAGE="#080d19", SIDEBAR="#0d1424", SURFACE="#080d19", CARD="#111a2e", CARD_BORDER="#243049",
        UP="#4fd08c", DOWN="#ff7d85", ACCENT="#3987e5",
        CARD_BACKGROUND="linear-gradient(150deg, rgba(57,135,229,0.16) 0%, rgba(17,26,46,0.92) 45%, rgba(13,20,36,0.96) 100%)",
        CARD_SHADOW="0 6px 22px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.05)",
    ),  # fmt: skip
    LIGHT: dict(
        SPOT="#3b3a37", FUTURES="#2a78d6", CARRY="#1baf7a", REVERSE="#eb6834", TBILL="#8a8983",
        INK="#0b0b0b", MUTED="#6f6e69", FAINT="#c9c8c2", GRID="#e8e7e2",
        PAGE="#fcfcfb", SIDEBAR="#f3f2ee", SURFACE="#fcfcfb", CARD="#ffffff", CARD_BORDER="#e3e2dc",
        UP="#12804f", DOWN="#c9343f", ACCENT="#2a78d6",
        CARD_BACKGROUND="linear-gradient(150deg, rgba(42,120,214,0.07) 0%, #ffffff 55%)",
        CARD_SHADOW="0 2px 10px rgba(20,30,50,0.07)",
    ),  # fmt: skip
}

# Module-level names, filled in by activate(). Chart code reads them at call time.
SPOT = FUTURES = CARRY = REVERSE = TBILL = INK = MUTED = FAINT = GRID = ""
PAGE = SIDEBAR = SURFACE = CARD = CARD_BORDER = UP = DOWN = ACCENT = CARD_BACKGROUND = CARD_SHADOW = ""
MODE = DARK

FONT = "Source Sans Pro, Segoe UI, Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'Cascadia Mono', Consolas, monospace"
TEMPLATE_NAME = "arb"


def activate(mode: str) -> None:
    """Switch every colour to `mode`, register the Plotly template and inject the CSS."""
    global MODE
    MODE = mode
    globals().update(PALETTES[mode])
    register_template()
    st.markdown(f"<style>{css()}</style>", unsafe_allow_html=True)


def sync_streamlit_theme(mode: str) -> None:
    """Make Streamlit's own widgets (inputs, tables, sidebar) follow the chosen theme.

    Streamlit reads its theme from config. Changing the options and re-running
    switches the built-in widgets too. Note the setting is per server, so on a
    shared deployment one viewer's toggle changes the theme for everyone.
    """
    palette = PALETTES[mode]
    wanted = {
        "theme.base": mode,
        "theme.backgroundColor": palette["PAGE"],
        "theme.secondaryBackgroundColor": palette["SIDEBAR"],
        "theme.textColor": palette["INK"],
        "theme.primaryColor": palette["ACCENT"],
    }
    if any(st.get_option(option) != value for option, value in wanted.items()):
        for option, value in wanted.items():
            st._config.set_option(option, value)
        st.rerun()


def register_template() -> None:
    axis = dict(
        gridcolor=GRID, gridwidth=0.6, linecolor=FAINT, zerolinecolor=FAINT,
        tickfont=dict(color=MUTED, size=12), title_font=dict(color=MUTED, size=12),
    )  # fmt: skip
    pio.templates[TEMPLATE_NAME] = go.layout.Template(
        layout=go.Layout(
            font=dict(family=FONT, color=INK, size=13),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            colorway=[FUTURES, CARRY, REVERSE, SPOT],
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis={**axis, "showgrid": False},
            yaxis=axis,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=12, color=INK), bgcolor="rgba(0,0,0,0)"),
            hovermode="x unified",
            hoverlabel=dict(bgcolor=CARD, bordercolor=CARD_BORDER, font=dict(family=FONT, size=12, color=INK)),
            hoverdistance=30,
            bargap=0.25,
        )
    )
    pio.templates.default = TEMPLATE_NAME


def hex_to_rgba(colour: str, alpha: float) -> str:
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{alpha})"


def css() -> str:
    """All custom styling. Animations are slow and subtle, and switch off for reduced-motion users."""
    glow = hex_to_rgba(ACCENT, 0.35)
    return f"""
@keyframes fade-in {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}
@keyframes ticker-scroll {{ from {{ transform: translateX(0); }} to {{ transform: translateX(-50%); }} }}
@keyframes badge-pulse {{ 0%, 100% {{ box-shadow: 0 0 0 0 var(--glow), 0 0 10px 0 var(--glow); }} 50% {{ box-shadow: 0 0 0 4px transparent, 0 0 18px 2px var(--glow); }} }}

.stApp {{ background: {PAGE}; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; animation: fade-in 0.5s ease-out; }}
[data-testid="stSidebar"] {{ background: {SIDEBAR}; border-right: 1px solid {CARD_BORDER}; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* Top banner with the ticker */
.banner {{ display: flex; align-items: center; gap: 16px; padding: 10px 16px; border-radius: 12px; border: 1px solid {CARD_BORDER};
           background: {CARD_BACKGROUND}; box-shadow: {CARD_SHADOW}; margin-bottom: 14px; }}
.brand {{ font-weight: 700; font-size: 1.02rem; letter-spacing: 0.02em; color: {INK}; white-space: nowrap; }}
.brand span {{ color: {ACCENT}; }}
.asof {{ font-size: 0.78rem; color: {MUTED}; white-space: nowrap; }}
.asof b {{ color: {INK}; }}
.ticker {{ flex: 1; min-width: 0; overflow: hidden; mask-image: linear-gradient(90deg, transparent, #000 6%, #000 94%, transparent);
           -webkit-mask-image: linear-gradient(90deg, transparent, #000 6%, #000 94%, transparent); }}
.ticker-track {{ display: inline-flex; gap: 34px; white-space: nowrap; animation: ticker-scroll 60s linear infinite; font-family: {MONO}; font-size: 0.8rem; color: {MUTED}; }}
.ticker:hover .ticker-track {{ animation-play-state: paused; }}
.ticker-track b {{ color: {INK}; font-weight: 600; }}
.live-dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: {UP}; margin-right: 6px; box-shadow: 0 0 8px {UP}; }}

/* KPI cards */
.kpi {{ border: 1px solid {CARD_BORDER}; border-radius: 12px; padding: 11px 13px 8px 13px; background: {CARD_BACKGROUND};
        box-shadow: {CARD_SHADOW}; backdrop-filter: blur(6px); min-height: 184px; margin-bottom: 14px; overflow: hidden; }}
.kpi-label {{ font-size: 0.7rem; color: {MUTED}; text-transform: uppercase; letter-spacing: 0.07em; }}
.kpi-value {{ font-size: 1.2rem; font-weight: 700; color: {INK}; line-height: 1.45; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.kpi-sub {{ font-size: 0.77rem; color: {MUTED}; line-height: 1.35; }}
.kpi-sub .greyed, .greyed {{ color: {TBILL}; }}
.kpi svg {{ display: block; width: 100%; height: 26px; margin-top: 6px; }}
.delta-up {{ color: {UP}; font-weight: 600; }}
.delta-down {{ color: {DOWN}; font-weight: 600; }}
.delta-flat {{ color: {MUTED}; }}
.unit {{ font-size: 0.8rem; font-weight: 400; color: {MUTED}; }}

/* Signal badge: a glowing pill with a slow pulse */
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px; color: #ffffff; font-size: 0.82rem; font-weight: 700;
          letter-spacing: 0.01em; animation: badge-pulse 2.8s ease-in-out infinite; }}

/* Tabs as pills (they wrap onto a second line on a narrow screen instead of scrolling) */
[data-testid="stTabs"] [role="tablist"] {{ gap: 7px; flex-wrap: wrap; padding: 2px 0 8px 0; border-bottom: none; overflow: visible; }}
[data-testid="stTabs"] > div {{ overflow: visible; }}
[data-testid="stTabs"] .react-aria-SelectionIndicator {{ display: none; }}
[data-testid="stTab"] {{ border-radius: 999px; padding: 4px 13px; border: 1px solid {CARD_BORDER}; background: {CARD}; color: {MUTED};
                         transition: background 0.2s, color 0.2s, border-color 0.2s; cursor: pointer; }}
[data-testid="stTab"] p {{ font-size: 0.86rem; color: inherit; }}
[data-testid="stTab"]:hover {{ color: {INK}; border-color: {ACCENT}; }}
[data-testid="stTab"][aria-selected="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; box-shadow: 0 0 14px {glow}; }}
[data-testid="stTab"][aria-selected="true"] p, [data-testid="stTab"][aria-selected="true"] span {{ color: #ffffff; }}

/* Section headers and takeaways */
.question {{ font-size: 1.05rem; font-weight: 650; color: {INK}; margin: 1rem 0 0.2rem 0; padding-bottom: 5px;
             border-bottom: 1px solid transparent; border-image: linear-gradient(90deg, {ACCENT}, transparent 70%) 1; }}
.takeaway {{ font-size: 0.9rem; color: {SPOT}; margin: 0 0 1.1rem 0; padding: 7px 11px; border-radius: 8px;
             background: {hex_to_rgba(ACCENT, 0.08)}; border-left: 3px solid {ACCENT}; }}
.insight {{ font-size: 0.93rem; color: {INK}; margin: 0.15rem 0; }}
.legend-note {{ font-size: 0.8rem; color: {MUTED}; }}
.lede {{ font-size: 1.02rem; color: {INK}; margin: 0.4rem 0 0.9rem 0; max-width: 1050px; }}
.finding-title {{ font-size: 1.0rem; font-weight: 600; margin: 0; min-height: 3em; color: {INK}; }}
.finding-body {{ font-size: 0.87rem; color: {SPOT}; margin: 0.25rem 0 0 0; min-height: 5.6em; }}
h2, h3, h4 {{ color: {INK}; }}

/* The trade diagram on the Method tab */
.flow {{ display: flex; align-items: stretch; gap: 6px; margin: 0.6rem 0 1rem 0; }}
.flow-step {{ flex: 1; border: 1px solid {CARD_BORDER}; border-radius: 10px; padding: 10px 12px; background: {CARD_BACKGROUND}; font-size: 0.86rem; color: {SPOT}; }}
.flow-step b {{ display: block; color: {INK}; }}
.flow-arrow {{ align-self: center; color: {MUTED}; font-size: 1.2rem; }}

/* The step-by-step calculator on the Signal Finder tab */
.calc {{ border: 1px solid {CARD_BORDER}; border-radius: 12px; padding: 12px 14px; background: {CARD_BACKGROUND}; box-shadow: {CARD_SHADOW}; }}
.calc h5 {{ margin: 0 0 6px 0; font-size: 0.98rem; color: {INK}; }}
.calc table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
.calc td {{ padding: 3px 4px; border: none; color: {SPOT}; vertical-align: top; }}
.calc td.formula {{ color: {MUTED}; font-size: 0.78rem; }}
.calc td.amount {{ text-align: right; font-family: {MONO}; font-variant-numeric: tabular-nums; white-space: nowrap; color: {INK}; }}
.calc tr.total td {{ border-top: 1px solid {CARD_BORDER}; font-weight: 700; color: {INK}; padding-top: 6px; }}
.calc .result {{ margin-top: 8px; font-size: 0.86rem; color: {SPOT}; }}
.calc .result b {{ color: {INK}; }}

@media (prefers-reduced-motion: reduce) {{
  .block-container, .ticker-track, .badge {{ animation: none; }}
}}
"""
