"""Save one full-page screenshot per tab and commodity, and check the layout at laptop width.

Start the app first, then run this from the project root:

    streamlit run app/app.py --server.headless true --server.port 8765
    python tests/take_screenshots.py

Needs Playwright (`pip install playwright`) and uses the Microsoft Edge already
installed on Windows, so no browser download is required. It is a review tool
and not a dependency of the dashboard.
"""

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

URL = "http://localhost:8765"
OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "screens"
COMMODITIES = ["JEERA", "GUARSEED"]
TAB_NAMES = [
    "Overview", "Signal Finder", "Historical Backtest", "Cash vs Futures", "Arb Economics", "Term Structure",
    "Convergence & Entry Timing", "Rollover & Capacity", "Method & Data",
]  # fmt: skip
WIDTH, HEIGHT = 1366, 768  # a common laptop screen
layout: dict[str, int] = {}  # screenshot file -> y position of the tab bar


def wait_until_drawn(page: Page) -> None:
    """Wait for Streamlit to finish re-running and for the charts to render."""
    page.wait_for_selector('[role="tab"]', timeout=60_000)
    try:  # give a re-run triggered by the last action a moment to start
        page.wait_for_selector('[data-testid="stApp"][data-test-script-state="running"]', timeout=1500)
    except PlaywrightTimeout:
        pass  # nothing re-ran, or it finished before we looked
    page.wait_for_selector('[data-testid="stApp"][data-test-script-state="notRunning"]', timeout=60_000)
    page.wait_for_timeout(1200)


def content_height(page: Page) -> int:
    """Height of the scrolling main area: Streamlit scrolls an inner container, not the page."""
    return page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollHeight")


def has_horizontal_scroll(page: Page) -> bool:
    return page.evaluate(
        """() => [document.documentElement, document.querySelector('[data-testid="stMain"]')]
                  .some(element => element.scrollWidth > element.clientWidth + 1)"""
    )


def shoot_tab(page: Page, commodity: str, index: int, name: str, suffix: str, problems: list[str]) -> None:
    """Open one tab, check it, and save a screenshot of its full height."""
    page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
    page.get_by_role("tab", name=name).click()
    wait_until_drawn(page)
    if has_horizontal_scroll(page):
        problems.append(f"{commodity} / {name}{suffix}: horizontal scrolling at {WIDTH}px")
    if page.locator('[data-testid="stException"]').count():
        problems.append(f"{commodity} / {name}{suffix}: exception shown on the page")

    # Grow the window to the full content height so one screenshot holds the whole tab.
    page.set_viewport_size({"width": WIDTH, "height": content_height(page) + 40})
    page.wait_for_timeout(1200)
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    path = OUT_DIR / f"{commodity.lower()}_{index}_{slug}{suffix}.png"
    page.screenshot(path=str(path))
    # Where the tab bar starts, so the guide can show a tab without repeating the header above it.
    layout[path.name] = round(page.locator('[role="tablist"]').first.bounding_box()["y"])
    print(f"saved {path.name}")


def set_light_theme(page: Page, on: bool) -> None:
    toggle = page.get_by_test_id("stSidebar").get_by_text("Light theme")
    checked = page.get_by_test_id("stSidebar").get_by_role("switch", name="Light theme").is_checked()
    if checked != on:
        toggle.click()
        page.wait_for_timeout(1500)  # the app re-runs twice: once for the toggle, once for the theme
        wait_until_drawn(page)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.on("console", lambda message: problems.append(f"console {message.type}: {message.text}") if message.type == "error" else None)

        for commodity in COMMODITIES:
            page.goto(f"{URL}/?commodity={commodity}")
            wait_until_drawn(page)
            for index, name in enumerate(TAB_NAMES):
                shoot_tab(page, commodity, index, name, "", problems)
            # The light theme, for the two tabs that open the demo.
            set_light_theme(page, True)
            for index, name in enumerate(TAB_NAMES[:2]):
                shoot_tab(page, commodity, index, name, "_light", problems)
            set_light_theme(page, False)  # the theme is a server setting: always leave it dark
        browser.close()
    (OUT_DIR / "layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")

    print("; ".join(problems) if problems else f"No problems: no errors and no horizontal scrolling at {WIDTH}px.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
